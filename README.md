# ServerControl
Aplicación destinada al control y monitorización de infraestructuras de servidores

# Instalación

## Instalar y ejecutar en Windows

~~~bash
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py --demo
~~~

## Instalar y ejecutar en Linux

~~~bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
sh launch.sh --demo
~~~

# Configuración

Ejemplo de fichero de configuración:
~~~json
[
  {
    "name": "mi-servidor",
    "host": "192.168.1.100",
    "port": 22,
    "user": "administrador",
    "os": "linux",
    "auth_method": "key",
    "key": "~/.ssh/id_ed25519",
    "known_hosts": "~/.ssh/known_hosts",
    "sudo": true,
    "services": [
      "nginx",
      "docker"
    ]
  }
]
~~~

Cuando se ejecuta `app.py` por primera vez, se genera un fichero de configuración que puede ser editable y guardado en el sistema.

