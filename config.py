import socket

# https://stackoverflow.com/a/28950776
def get_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(0)
    try:
        s.connect(("10.254.254.254", 1))
        IP = s.getsockname()[0]
    except Exception:
        IP = "127.0.0.1"
    finally:
        s.close()
    return IP


ip = get_ip()
port = 8000
response_compression = True  # True answers lz77 requests with lz77; only worth it over a slow link
verbose_log = True
reload = True  # set False on a production server; restarts on code change drop game connections
keep_alive = 300  # seconds an idle game connection stays open (games reuse HTTP/1.1 connections)

arcade = "Manhwa"
paseli = 10000
maintenance_mode = False
