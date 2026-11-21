from asistente.sesiones import SesionInfo, elegir, titulo_hablado

S = [
    SesionInfo("a", "Asistente de voz: sesiones y control del PC", "/x", 300.0),
    SesionInfo("b", "Revisar la guía de NAS de Minibestia", "/x", 200.0),
    SesionInfo("c", "Arreglar la indexación de Movicio en Search Console", "/y", 100.0),
    SesionInfo("d", "Otra guía de NAS para homelab", "/x", 50.0),
]


def test_titulo_hablado():
    info = SesionInfo("z", "Arreglar `wp sitio` **ahora** mismo con las pruebas del catálogo de modelos", "/x", 1)
    t = titulo_hablado(info)
    assert "`" not in t and "*" not in t
    assert len(t.split()) == 9
    assert titulo_hablado(SesionInfo("z", "Hola\nmundo", "/x", 1)) == "Hola mundo"


def test_elegir_por_numero():
    assert elegir(S, "dos", S[:3]).id == "b"
    assert elegir(S, "la sesión número 3", S[:3]).id == "c"
    assert elegir(S, "la primera", S[:3]).id == "a"
    assert elegir(S, "la última", S[:3]).id == "a"
    assert elegir(S, "cinco", S[:3]) is None


def test_elegir_numero_sin_lista_usa_las_cinco_primeras():
    assert elegir(S, "4", []).id == "d"


def test_elegir_por_titulo():
    assert elegir(S, "del asistente de voz", []).id == "a"
    assert elegir(S, "la de movicio", []).id == "c"
    assert elegir(S, "indexacion", []).id == "c"


def test_elegir_empate_gana_la_mas_reciente():
    assert elegir(S, "guia nas", []).id == "b"


def test_elegir_sin_coincidencia():
    assert elegir(S, "zanahorias", []) is None
    assert elegir(S, "", []) is None
