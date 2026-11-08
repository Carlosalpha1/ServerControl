"""Túneles SSH locales sin depender de ejecutables externos.

Se usan lectores separados para sockets y canales Paramiko porque
select(channel) no es portable a Windows. Los buffers y tiempos de espera
limitados permiten interrumpir las transferencias al cerrar un túnel.
"""

from __future__ import annotations

import errno
import logging
import socket
from dataclasses import dataclass, replace
from threading import Event, Lock, Thread, current_thread
from typing import Any
from uuid import uuid4

from core import SSH

LOGGER = logging.getLogger("servercontrol.tunnels")

LOOPBACK = "127.0.0.1"
BUFFER_SIZE = 65536
IO_TIMEOUT = 0.25
MAX_CONNECTIONS = 32


@dataclass(frozen=True)
class TunnelInfo:
    """Estado inmutable sin credenciales, seguro para leer desde Qt."""

    id: str
    server: str
    remote_port: int
    local_port: int
    state: str
    error: str = ""

    @property
    def endpoint(self) -> str:
        return f"{LOOPBACK}:{self.local_port}"

    @property
    def status(self) -> str:
        return self.state


class _Cancellation:
    def __init__(self, local: Event, external: Event | None):
        self.local = local
        self.external = external

    def is_set(self) -> bool:
        return self.local.is_set() or (
            self.external is not None and self.external.is_set()
        )


class _Tunnel:
    def __init__(
        self,
        info: TunnelInfo,
        external_cancel: Event | None,
    ):
        self.info = info
        self.stop = Event()
        self.cancel = _Cancellation(self.stop, external_cancel)
        self.lock = Lock()

        self.ssh: SSH | None = None
        self.listener: socket.socket | None = None
        self.transport: Any = None
        self.thread: Thread | None = None

        self.connections: set[
            tuple[socket.socket, Any]
        ] = set()

        self.workers: set[Thread] = set()

    def update(self, **changes: Any) -> TunnelInfo:
        with self.lock:
            self.info = replace(self.info, **changes)
            return self.info

    def snapshot(self) -> TunnelInfo:
        with self.lock:
            return self.info

    def shutdown(self, *, error: str = "") -> None:
        self.stop.set()

        with self.lock:
            self.info = replace(
                self.info,
                state="error" if error else "closed",
                error=error,
            )

            listener = self.listener
            ssh = self.ssh
            connections = tuple(self.connections)

        if listener is not None:
            # Despierta accept() inmediatamente para agilizar el cierre.
            try:
                listener.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

            listener.close()

        for local, channel in connections:
            _close_pair(local, channel)

        if ssh is not None:
            # El cierre puede coincidir con una autenticación en curso.
            # La comprobación de cancelación cerrará el cliente al terminar.
            if ssh.client is not None:
                ssh.client.close()

            ssh.password = ""


def _close_pair(
    local: socket.socket,
    channel: Any,
) -> None:
    for endpoint in (local, channel):
        try:
            endpoint.close()
        except (OSError, EOFError):
            LOGGER.debug(
                "El extremo de la conexión ya estaba cerrado",
                exc_info=True,
            )


def _listener(preferred_port: int) -> socket.socket:
    listener = socket.socket(
        socket.AF_INET,
        socket.SOCK_STREAM,
    )

    try:
        # Windows necesita exclusividad explícita para impedir que otro
        # proceso comparta este puerto usando SO_REUSEADDR.
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            listener.setsockopt(
                socket.SOL_SOCKET,
                socket.SO_EXCLUSIVEADDRUSE,
                1,
            )

        try:
            listener.bind((LOOPBACK, preferred_port))
        except OSError as exc:
            if (
                exc.errno not in (
                    errno.EADDRINUSE,
                    errno.EACCES,
                )
                and getattr(exc, "winerror", None)
                not in (10048, 10013)
            ):
                raise

            # El sistema elige un puerto libre y lo reserva directamente.
            listener.bind((LOOPBACK, 0))

        listener.listen(MAX_CONNECTIONS)
        listener.settimeout(IO_TIMEOUT)

        return listener

    except BaseException:
        listener.close()
        raise


class TunnelManager:
    """Gestiona sesiones persistentes de reenvío.

    open() debe ejecutarse en segundo plano. Un túnel activo confirma la
    conexión SSH y la escucha local. El servicio remoto se contacta cuando
    un cliente utiliza el túnel.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._tunnels: dict[str, _Tunnel] = {}
        self._opening: set[tuple[str, int]] = set()

    def open(
        self,
        server: dict[str, Any],
        remote_port: int,
        password: str = "",
        cancel_event: Event | None = None,
    ) -> TunnelInfo:
        if (
            type(remote_port) is not int
            or not 1 <= remote_port <= 65535
        ):
            raise ValueError(
                "El puerto debe ser un entero entre 1 y 65535."
            )

        name = server.get("name")

        if not isinstance(name, str) or not name.strip():
            raise ValueError(
                "El servidor debe tener un nombre."
            )

        key = (name, remote_port)

        with self._lock:
            for existing in self._tunnels.values():
                info = existing.snapshot()

                if (
                    (info.server, info.remote_port) == key
                    and info.state == "active"
                ):
                    if (
                        not existing.cancel.is_set()
                        and existing.transport.is_active()
                    ):
                        return info

            if key in self._opening:
                raise RuntimeError(
                    "Ya se está creando un túnel "
                    "para este servidor y puerto."
                )

            self._opening.add(key)

            tunnel = _Tunnel(
                TunnelInfo(
                    uuid4().hex,
                    name,
                    remote_port,
                    0,
                    "connecting",
                ),
                cancel_event,
            )

            self._tunnels[tunnel.info.id] = tunnel

        try:
            if tunnel.cancel.is_set():
                raise ConnectionError(
                    "Creación del túnel cancelada."
                )

            listener = _listener(remote_port)

            with tunnel.lock:
                tunnel.listener = listener
                tunnel.info = replace(
                    tunnel.info,
                    local_port=listener.getsockname()[1],
                )

            ssh = SSH(dict(server), password, tunnel.cancel)

            with tunnel.lock:
                tunnel.ssh = ssh

            ssh.__enter__()
            ssh.password = ""

            transport = ssh.client.get_transport()

            if transport is None or not transport.is_active():
                raise ConnectionError(
                    "El transporte SSH no está activo."
                )

            transport.set_keepalive(30)
            tunnel.transport = transport

            if tunnel.cancel.is_set():
                raise ConnectionError(
                    "Creación del túnel cancelada."
                )

            thread = Thread(
                target=self._serve,
                args=(tunnel,),
                name=f"tunnel-{name}-{remote_port}",
                daemon=True,
            )

            with tunnel.lock:
                if tunnel.cancel.is_set():
                    raise ConnectionError(
                        "Creación del túnel cancelada."
                    )

                tunnel.info = replace(
                    tunnel.info,
                    state="active",
                    error="",
                )

                tunnel.thread = thread
                thread.start()

            LOGGER.info(
                "Túnel SSH abierto para %s, puerto %s",
                name,
                remote_port,
            )

            return tunnel.snapshot()

        except Exception as exc:
            cancelled = tunnel.cancel.is_set()

            tunnel.shutdown(
                error="" if cancelled else str(exc)
            )

            LOGGER.debug(
                "No se pudo crear el túnel para %s",
                name,
                exc_info=True,
            )

            raise

        finally:
            password = ""

            with self._lock:
                self._opening.discard(key)

    def list(
        self,
        server_name: str | None = None,
    ) -> list[TunnelInfo]:
        with self._lock:
            tunnels = tuple(self._tunnels.values())

        return [
            info
            for tunnel in tunnels
            if (
                (info := tunnel.snapshot()).server == server_name
                or server_name is None
            )
        ]

    def close(self, tunnel_id: str) -> None:
        with self._lock:
            tunnel = self._tunnels.get(tunnel_id)

        if tunnel is None:
            return

        tunnel.shutdown()
        thread = tunnel.thread

        if thread is not None and thread is not current_thread():
            thread.join(timeout=0.4)

    def close_server(self, server_name: str) -> None:
        for info in self.list(server_name):
            if info.state in ("active", "connecting"):
                self.close(info.id)

    def close_all(self) -> None:
        for info in self.list():
            if info.state in ("active", "connecting"):
                self.close(info.id)

    def _serve(self, tunnel: _Tunnel) -> None:
        failure = ""

        try:
            while not tunnel.cancel.is_set():
                if not tunnel.transport.is_active():
                    failure = (
                        "Se ha perdido la conexión SSH. "
                        "Vuelve a crear el túnel."
                    )
                    break

                try:
                    local, origin = tunnel.listener.accept()
                except socket.timeout:
                    continue

                with tunnel.lock:
                    if len(tunnel.workers) >= MAX_CONNECTIONS:
                        local.close()

                        tunnel.info = replace(
                            tunnel.info,
                            error=(
                                "Se alcanzó el límite de "
                                "32 conexiones simultáneas."
                            ),
                        )
                        continue

                    worker = Thread(
                        target=self._forward,
                        args=(tunnel, local, origin),
                        daemon=True,
                    )

                    tunnel.workers.add(worker)

                worker.start()

        except OSError as exc:
            if not tunnel.cancel.is_set():
                failure = str(exc)

        except Exception as exc:
            failure = str(exc)
            LOGGER.exception(
                "El listener del túnel ha fallado"
            )

        finally:
            # No se sobrescribe un cierre explícito concurrente.
            if not tunnel.stop.is_set():
                tunnel.shutdown(error=failure)

    def _forward(
        self,
        tunnel: _Tunnel,
        local: socket.socket,
        origin: tuple[str, int],
    ) -> None:
        channel = None
        pair = None
        stream_stop = Event()
        reverse = None

        try:
            channel = tunnel.transport.open_channel(
                "direct-tcpip",
                (LOOPBACK, tunnel.info.remote_port),
                origin,
                timeout=8,
            )

            if channel is None:
                raise ConnectionError(
                    "El servidor SSH no abrió el canal solicitado."
                )

            if tunnel.cancel.is_set():
                return

            local.settimeout(IO_TIMEOUT)
            channel.settimeout(IO_TIMEOUT)

            pair = (local, channel)

            with tunnel.lock:
                tunnel.connections.add(pair)
                tunnel.info = replace(
                    tunnel.info,
                    error="",
                )

            reverse = Thread(
                target=self._pump,
                args=(
                    channel,
                    local,
                    tunnel,
                    stream_stop,
                    False,
                ),
                daemon=True,
            )

            reverse.start()

            self._pump(
                local,
                channel,
                tunnel,
                stream_stop,
                True,
            )

            while (
                reverse.is_alive()
                and not tunnel.cancel.is_set()
                and not stream_stop.is_set()
            ):
                reverse.join(timeout=IO_TIMEOUT)

        except Exception as exc:
            if not tunnel.cancel.is_set():
                tunnel.update(
                    error=(
                        "No se pudo acceder al puerto remoto: "
                        + str(exc)
                    )
                )

                LOGGER.debug(
                    "El canal de reenvío ha fallado",
                    exc_info=True,
                )

        finally:
            stream_stop.set()

            if channel is None:
                local.close()
            else:
                _close_pair(local, channel)

            if reverse is not None:
                reverse.join(timeout=0.4)

            with tunnel.lock:
                if pair is not None:
                    tunnel.connections.discard(pair)

                tunnel.workers.discard(current_thread())

    @staticmethod
    def _pump(
        source: Any,
        target: Any,
        tunnel: _Tunnel,
        stream_stop: Event,
        to_channel: bool,
    ) -> None:
        try:
            while (
                not tunnel.cancel.is_set()
                and not stream_stop.is_set()
            ):
                try:
                    data = source.recv(BUFFER_SIZE)
                except socket.timeout:
                    continue

                if not data:
                    # Se conserva el cierre parcial TCP: algunos protocolos
                    # envían la respuesta después del EOF de la petición.
                    if to_channel:
                        target.shutdown_write()
                    else:
                        target.shutdown(socket.SHUT_WR)

                    return

                offset = 0

                while (
                    offset < len(data)
                    and not tunnel.cancel.is_set()
                    and not stream_stop.is_set()
                ):
                    try:
                        sent = target.send(data[offset:])
                    except socket.timeout:
                        continue

                    if sent <= 0:
                        raise ConnectionError(
                            "El extremo del túnel se ha cerrado."
                        )

                    offset += sent

        except (OSError, EOFError, ConnectionError) as exc:
            if (
                not tunnel.cancel.is_set()
                and not stream_stop.is_set()
            ):
                tunnel.update(
                    error="Transferencia interrumpida: " + str(exc)
                )

                LOGGER.debug(
                    "Transferencia interrumpida",
                    exc_info=True,
                )

            stream_stop.set()