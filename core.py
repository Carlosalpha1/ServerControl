"""Configuración, comandos validados y transporte SSH, independientes de Qt."""

from __future__ import annotations

import base64
import json
import logging
import math
import os
import re
import shlex
import sys
import time
from pathlib import Path
from threading import Event
from typing import Any

LOG = logging.getLogger("servercontrol.core")
logging.getLogger("servercontrol").addHandler(logging.NullHandler())

NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.@:-]{0,127}\Z")
ACTIONS = {"start", "stop", "restart"}
COMMAND_TIMEOUT = 20
MAX_OUTPUT = 2_000_000

Server = dict[str, Any]


def config_directory() -> Path:
    """Devuelve la ubicación de configuración correspondiente al sistema."""
    if sys.platform == "win32":
        root = Path(
            os.environ.get("APPDATA")
            or os.environ.get("LOCALAPPDATA")
            or Path.home() / "AppData" / "Roaming"
        )
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME", "")
        root = (
            Path(xdg)
            if xdg and Path(xdg).is_absolute()
            else Path.home() / ".config"
        )

    return root / "ServerControl"


def service_name(name: str) -> str:
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ValueError(
            "Nombre de servicio no válido: "
            "use letras, números, _, ., @, : o -"
        )
    return name


def validate(servers: list[Server]) -> list[Server]:
    if not isinstance(servers, list):
        raise ValueError("La configuración debe ser una lista JSON")

    seen = set()

    for server in servers:
        if not isinstance(server, dict):
            raise ValueError("Cada servidor debe ser un objeto")

        for key in ("name", "host", "user", "os"):
            value = server.get(key)

            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Falta {key}")

            if any(ord(char) < 32 or ord(char) == 127 for char in value):
                raise ValueError(f"{key} contiene caracteres de control")

        if server["name"] in seen:
            raise ValueError("Los nombres de servidores deben ser únicos")

        seen.add(server["name"])

        if server["os"] not in ("linux", "windows"):
            raise ValueError("os debe ser linux o windows")

        if server.get("auth_method", "auto") not in (
            "auto", "password", "key"
        ):
            raise ValueError("auth_method debe ser auto, password o key")

        if server.get("auth_method") == "key" and not server.get("key"):
            raise ValueError(
                "Indique key para autenticar mediante clave privada"
            )

        port = server.get("port", 22)
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("Puerto no válido")

        if (
            not isinstance(server.get("services"), list)
            or not server["services"]
        ):
            raise ValueError("Configure al menos un servicio")

        for name in server["services"]:
            service_name(name)

        if len(set(server["services"])) != len(server["services"]):
            raise ValueError("Servicios duplicados")

        if any(key in server for key in ("password", "passphrase")):
            raise ValueError(
                "No guarde contraseñas en el archivo; "
                "introdúzcalas en Conectar"
            )

        for key in ("key", "known_hosts"):
            if key in server and not isinstance(server[key], str):
                raise ValueError(f"{key} debe ser una ruta de texto")

        if "sudo" in server and type(server["sudo"]) is not bool:
            raise ValueError("sudo debe ser true o false")

    return servers


def ps(script: str) -> str:
    """Codifica PowerShell para evitar problemas de comillas en OpenSSH."""
    payload = base64.b64encode(
        (
            "$ErrorActionPreference='Stop'; "
            "[Console]::OutputEncoding="
            "[System.Text.UTF8Encoding]::new($false); "
            "$OutputEncoding=[Console]::OutputEncoding; "
            + script
        ).encode("utf-16le")
    ).decode("ascii")

    return (
        "powershell.exe -NoLogo -NoProfile -NonInteractive "
        "-EncodedCommand " + payload
    )


def action_command(server: Server, service: str, action: str) -> str:
    service_name(service)

    if service not in server["services"] or action not in ACTIONS:
        raise ValueError(
            "Acción o servicio no autorizado por la configuración"
        )

    if server["os"] == "windows":
        verb = {
            "start": "Start",
            "stop": "Stop",
            "restart": "Restart",
        }[action]

        return ps(
            f"{verb}-Service -Name '{service}'; "
            "'Solicitud completada'"
        )

    prefix = "sudo -n " if server.get("sudo") else ""
    return prefix + f"systemctl {action} -- {shlex.quote(service)}"


LINUX_METRICS = """import json,time,shutil
def cpu():
    with open('/proc/stat') as f:
        a=list(map(int,f.readline().split()[1:9]))
    return sum(a),a[3]+a[4]
a=cpu()
time.sleep(0.3)
b=cpu()
with open('/proc/meminfo') as f:
    m={k:int(v.split()[0]) for k,v in (l.split(':',1) for l in f)}
d=shutil.disk_usage('/')
print(json.dumps({
    'cpu':round(100*(1-(b[1]-a[1])/max(1,b[0]-a[0])),1),
    'memory':round(100*(1-m['MemAvailable']/m['MemTotal']),1),
    'disk':round(d.used/d.total*100,1)
}))
"""

WINDOWS_METRICS = (
    "$o=Get-CimInstance Win32_OperatingSystem; "
    "$c=Get-CimInstance Win32_Processor "
    "| Measure-Object LoadPercentage -Average; "
    "$d=Get-CimInstance Win32_LogicalDisk "
    "-Filter \"DeviceID='$($env:SystemDrive)'\"; "
    "@{cpu=$c.Average;"
    "memory=(100*(1-$o.FreePhysicalMemory/$o.TotalVisibleMemorySize));"
    "disk=(100*(1-$d.FreeSpace/$d.Size))}"
    "|ConvertTo-Json -Compress"
)


def validate_metrics(raw: Any) -> dict[str, float]:
    """Impide que valores inválidos o NaN lleguen a las barras de Qt."""
    if not isinstance(raw, dict):
        raise ValueError("Respuesta de métricas no válida")

    result = {}

    for key in ("cpu", "memory", "disk"):
        value = raw.get(key)

        if value is None:
            continue

        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ValueError(f"Métrica {key} no válida")

        result[key] = max(0.0, min(100.0, float(value)))

    return result


class SSH:
    """Conexión SSH verificada con comandos limitados y cancelables."""

    def __init__(
        self,
        server: Server,
        password: str = "",
        cancel_event: Event | None = None,
    ):
        self.cancel_event = cancel_event
        self.server = server
        self.password = password
        self.client = None

    def __enter__(self) -> SSH:
        self.check_cancelled()

        import paramiko

        self.client = paramiko.SSHClient()

        try:
            # También se libera el cliente si falla la lectura de known_hosts.
            self.client.load_system_host_keys()

            known = Path(
                self.server.get("known_hosts") or "~/.ssh/known_hosts"
            ).expanduser()

            if known.exists():
                self.client.load_host_keys(str(known))

            self.client.set_missing_host_key_policy(
                paramiko.RejectPolicy()
            )

            mode = self.server.get("auth_method", "auto")

            self.client.connect(
                self.server["host"],
                port=self.server.get("port", 22),
                username=self.server["user"],
                password=(
                    self.password or None
                    if mode != "key"
                    else None
                ),
                passphrase=(
                    self.password or None
                    if mode != "password"
                    else None
                ),
                key_filename=(
                    str(Path(self.server["key"]).expanduser())
                    if self.server.get("key") and mode != "password"
                    else None
                ),
                timeout=8,
                auth_timeout=10,
                banner_timeout=8,
                allow_agent=mode == "auto",
                look_for_keys=mode == "auto",
            )

            self.check_cancelled()

        except Exception:
            self.client.close()
            self.password = ""
            raise

        # Paramiko ya ha consumido la credencial.
        self.password = ""
        return self

    def __exit__(self, *_: Any) -> None:
        if self.client is not None:
            self.client.close()
        self.password = ""

    def check_cancelled(self) -> None:
        if self.cancel_event is not None and self.cancel_event.is_set():
            if self.client is not None:
                self.client.close()

            self.password = ""
            raise ConnectionError("SSH desconectado manualmente")

    def run(
        self,
        command: str,
        accepted: tuple[int, ...] = (0,),
    ) -> str:
        self.check_cancelled()

        stdin, stdout, stderr = self.client.exec_command(
            command, timeout=18
        )
        stdin.close()

        channel = stdout.channel
        out = bytearray()
        err = bytearray()
        deadline = time.monotonic() + COMMAND_TIMEOUT

        try:
            # Se vacían stdout y stderr antes de esperar al código de salida.
            # Esperar primero puede bloquear el canal si se llena su ventana.
            while True:
                self.check_cancelled()

                if channel.recv_ready():
                    out.extend(channel.recv(32768))

                if channel.recv_stderr_ready():
                    err.extend(channel.recv_stderr(32768))

                if len(out) + len(err) > MAX_OUTPUT:
                    raise RuntimeError("Respuesta SSH demasiado grande")

                if (
                    channel.exit_status_ready()
                    and not channel.recv_ready()
                    and not channel.recv_stderr_ready()
                ):
                    break

                if time.monotonic() > deadline:
                    raise TimeoutError(
                        f"El comando SSH superó {COMMAND_TIMEOUT} segundos"
                    )

                time.sleep(0.01)

            code = channel.recv_exit_status()

            if code not in accepted:
                raise RuntimeError(
                    err.decode(errors="replace").strip()
                    or out.decode(errors="replace").strip()
                    or f"Comando fallido ({code})"
                )

            return out.decode(
                "utf-8-sig", errors="replace"
            ).strip()

        finally:
            channel.close()

    def service_states(self) -> dict[str, str]:
        names = [
            service_name(name)
            for name in self.server["services"]
        ]

        if self.server["os"] == "windows":
            quoted = ",".join(f"'{name}'" for name in names)

            command = ps(
                "$result=[ordered]@{}; "
                f"foreach($name in @({quoted})){{"
                "$s=Get-Service -Name $name "
                "-ErrorAction SilentlyContinue; "
                "$result[$name]=if($null -eq $s)"
                "{'not-found'}else{$s.Status.ToString()}"
                "}; $result|ConvertTo-Json -Compress"
            )

            raw = json.loads(self.run(command))

            if not isinstance(raw, dict):
                raise ValueError("Respuesta de servicios no válida")

            return {
                name: str(raw.get(name, "unknown"))
                for name in names
            }

        if len(names) == 1:
            return {
                names[0]: self.run(
                    "systemctl show --property=ActiveState --value -- "
                    + shlex.quote(names[0])
                ) or "unknown"
            }

        # Un canal para todos los servicios reduce los viajes de red.
        # Los nombres se validan y se escapan antes de crear el comando.
        units = " ".join(shlex.quote(name) for name in names)

        command = (
            f"for unit in {units}; do "
            'state=$(systemctl show --property=ActiveState '
            '--value -- "$unit" 2>/dev/null) '
            '|| state=not-found; '
            'printf \'%s\\t%s\\n\' "$unit" "${state:-unknown}"; done'
        )

        raw = dict(
            line.split("\t", 1)
            for line in self.run(command).splitlines()
            if "\t" in line
        )

        return {
            name: raw.get(name, "unknown")
            for name in names
        }

    def snapshot(self) -> dict[str, Any]:
        result = {
            "services": self.service_states(),
            "metrics": {},
            "warnings": [],
        }

        command = (
            ps(WINDOWS_METRICS)
            if self.server["os"] == "windows"
            else "python3 -c " + shlex.quote(LINUX_METRICS)
        )

        try:
            result["metrics"] = validate_metrics(
                json.loads(self.run(command))
            )
        except Exception as exc:
            # Un fallo de métricas no debe ocultar los servicios.
            result["warnings"].append(
                "Métricas no disponibles: " + str(exc)
            )

            LOG.debug(
                "Métricas no disponibles para %s",
                self.server.get("name"),
                exc_info=True,
            )

        return result

    def logs(self, name: str) -> str:
        service_name(name)

        if name not in self.server["services"]:
            raise ValueError("Servicio no configurado")

        if self.server["os"] == "linux":
            return self.run(
                "journalctl --no-pager -n 100 -o short-iso -u "
                + shlex.quote(name)
            )

        return self.run(
            ps(
                "$e=Get-WinEvent -FilterHashtable "
                "@{LogName='System';"
                "ProviderName='Service Control Manager'} "
                "-MaxEvents 500 -ErrorAction SilentlyContinue; "
                f"$e | Where-Object "
                f"{{$_.Message -match [regex]::Escape('{name}')}} | "
                "Select-Object -First 100 TimeCreated,Id,Message | "
                "Format-List | Out-String -Width 240"
            )
        )