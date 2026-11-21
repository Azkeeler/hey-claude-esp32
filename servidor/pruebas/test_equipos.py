import pytest

from asistente.equipos import (
    es_basura,
    extraer_video,
    ip_por_mac,
    leer_desktop,
    mejor_app,
    normalizar_nombre,
    paquete_magico,
    url_valida,
)
from asistente.herramientas import describir


def test_normalizar_nombre():
    assert normalizar_nombre("Aion dos") == "aion 2"
    assert normalizar_nombre("Fútbol  Diez!") == "futbol 10"


def test_mejor_app_exacta_y_aproximada():
    nombres = ["AION 2", "AION", "Google Chrome", "Steam", "Desinstalar Algo"]
    assert mejor_app("aion dos", nombres)[0] == "AION 2"
    assert mejor_app("chrome", nombres)[0] == "Google Chrome"
    assert mejor_app("stean", nombres)[0] == "Steam"


def test_mejor_app_empate_el_mas_corto():
    assert mejor_app("aion", ["AION 2", "AION Classic", "AION"])[0] == "AION"


def test_mejor_app_sin_resultado_da_parecidos():
    elegida, parecidos = mejor_app("zzzz", ["Steam", "Chrome"])
    assert elegida is None and len(parecidos) <= 3
    assert mejor_app("", ["Steam"]) == (None, [])


def test_es_basura():
    for n in ("Desinstalar Foo", "Foo Documentation", "Léeme README", "Foo on the Web", "Ayuda FAQ", "Samples"):
        assert es_basura(n), n
    assert not es_basura("Steam")


def test_url_valida():
    assert url_valida("https://www.youtube.com/watch?v=abc")
    assert url_valida("http://localhost:8080/x")
    for u in ("ftp://x.com", "javascript:alert(1)", "https://a b.com", "https://x.com/'", "https://x.com/`",
              "https://x.com/$(id)", "https://x.com/<a>", 'https://x.com/"', "https://x.com/\x07",
              "https://x.com/" + "a" * 2000, ""):
        assert not url_valida(u), u


def test_paquete_magico():
    p = paquete_magico("aa:bb:cc:00:00:02")
    assert len(p) == 102
    assert p[:6] == b"\xff" * 6
    assert p[6:12] == bytes.fromhex("aabbcc000002") and p[-6:] == bytes.fromhex("aabbcc000002")
    with pytest.raises(ValueError):
        paquete_magico("12:34")


def test_leer_desktop():
    base = "[Desktop Entry]\nType=Application\nName=Firefox\nName[es]=Firefox Web\n"
    assert leer_desktop(base) == {"nombre": "Firefox Web"}
    assert leer_desktop(base + "NoDisplay=true\n") is None
    assert leer_desktop(base + "Hidden=true\n") is None
    assert leer_desktop("[Desktop Entry]\nType=Link\nName=Algo\n") is None
    assert leer_desktop("[Desktop Entry]\nType=Application\nName=Solo\n[Desktop Action x]\nName=Otro\n") == {
        "nombre": "Solo"
    }


def test_extraer_video():
    html = (
        'x{"videoRenderer":{"videoId":"abcDEF12345","thumbnail":{},'
        '"title":{"runs":[{"text":"Hola \\u00e1 \\"mundo\\""}]}}'
    )
    assert extraer_video(html) == ("abcDEF12345", 'Hola á "mundo"')
    assert extraer_video("<html></html>") is None


def test_ip_por_mac():
    salida = (
        "192.168.1.1 dev eno1 lladdr 00:11:22:33:44:55 REACHABLE\n"
        "192.168.1.200 dev eno1 lladdr AA:BB:CC:00:00:02 STALE\n"
    )
    assert ip_por_mac(salida, "aa:bb:cc:00:00:02") == "192.168.1.200"
    assert ip_por_mac(salida, "aa:bb:cc:dd:ee:ff") is None


def test_describir():
    assert describir("mcp__pc__cerrar_app", {"nombre": "Chrome"}) == "cerrar Chrome en el PC grande"
    assert describir("mcp__pc__cerrar_app", {"nombre": "Steam", "equipo": "mini"}) == "cerrar Steam en el mini PC"
    assert describir("mcp__pc__apagar_equipo", {"equipo": "grande", "modo": "apagar"}) == "apagar el PC grande"
    assert describir("mcp__pc__apagar_equipo", {"modo": "suspender"}) == "suspender el PC grande"


def test_describir_bash_y_clave():
    from asistente.claude import _clave_entrada
    from asistente.herramientas import describir

    d = describir("Bash", {"command": "git status", "description": "Ver el estado del repositorio"})
    assert d == "ejecutar un comando: Ver el estado del repositorio"
    a = _clave_entrada({"command": "git status", "description": "uno"})
    assert a == _clave_entrada({"command": "git status", "description": "otra descripción"})
    assert a != _clave_entrada({"command": "git status; rm -rf x"})
