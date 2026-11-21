from asistente.frases import Troceador, limpiar_markdown


def trocear(*trozos: str) -> list[str]:
    t = Troceador()
    r: list[str] = []
    for x in trozos:
        r += t.alimentar(x)
    return r + t.terminar()


def test_frases_simples():
    assert trocear("Hola. ¿Qué tal? Muy bien!") == ["Hola.", "¿Qué tal?", "Muy bien!"]


def test_streaming_por_trozos():
    assert trocear("Hola, mu", "ndo. Ad", "iós.") == ["Hola, mundo.", "Adiós."]


def test_no_corta_numeros():
    assert trocear("Mide 3.5 pulgadas. Pesa 2.25 kg.") == ["Mide 3.5 pulgadas.", "Pesa 2.25 kg."]


def test_no_corta_abreviaturas():
    assert trocear("Hay varios, p. ej. el Minisforum, etc. Y más cosas.") == [
        "Hay varios, p. ej. el Minisforum, etc. Y más cosas."
    ]
    assert trocear("El Sr. García llamó. Luego colgó.") == ["El Sr. García llamó.", "Luego colgó."]


def test_salto_de_linea_corta():
    assert trocear("Primera línea\nSegunda línea\n") == ["Primera línea", "Segunda línea"]


def test_dos_puntos():
    assert trocear("Mira esto: es bueno.") == ["Mira esto:", "es bueno."]


def test_frase_larga_se_corta_en_coma():
    larga = ", ".join(["palabra"] * 40) + "."
    partes = trocear(larga)
    assert len(partes) > 1
    assert all(len(p) <= 200 for p in partes)
    assert partes[0].endswith(",")


def test_markdown():
    assert limpiar_markdown("**Hola** `mundo`") == "Hola mundo"
    assert limpiar_markdown("# Título") == "Título"
    assert limpiar_markdown("- elemento uno") == "elemento uno"
    assert limpiar_markdown("Mira [este sitio](https://example.com/a)") == "Mira este sitio"
    assert limpiar_markdown("Entra en https://example.com/x/y ahora") == "Entra en un enlace ahora"


def test_descarta_lo_que_no_se_pronuncia():
    assert trocear("---\n```\n**Hecho.**") == ["Hecho."]


def test_markdown_en_streaming():
    assert trocear("**Hola", " mundo**. Fin.") == ["Hola mundo.", "Fin."]
