from asistente.config import Proyecto
from asistente.ordenes import interpretar

PROYECTOS = [
    Proyecto("minibestia", "/x", ["mini bestia", "web", "subnicho"]),
    Proyecto("movicio", "/y", ["consolas", "movi"]),
]


def test_cambiar_proyecto():
    for frase in ("Cambia al proyecto movicio", "cambiar de proyecto Movicio.", "proyecto movicio", "Pasa a consolas"):
        o = interpretar(frase, PROYECTOS)
        assert o and o.tipo == "proyecto" and o.proyecto == "movicio", frase


def test_aproximado():
    o = interpretar("cambia al proyecto mini bestias", PROYECTOS)
    assert o and o.proyecto == "minibestia"
    o = interpretar("cambia al proyecto Movicio.", PROYECTOS)
    assert o and o.proyecto == "movicio"


def test_proyecto_desconocido():
    o = interpretar("cambia al proyecto zanahoria", PROYECTOS)
    assert o and o.tipo == "proyecto_desconocido"


def test_nueva_conversacion():
    for frase in ("Nueva conversación", "empieza de cero", "Olvida lo anterior."):
        o = interpretar(frase, PROYECTOS)
        assert o and o.tipo == "nueva", frase


def test_donde():
    o = interpretar("¿En qué proyecto estás?", PROYECTOS)
    assert o and o.tipo == "donde"


def test_parar():
    for frase in ("Para", "Cállate", "silencio."):
        o = interpretar(frase, PROYECTOS)
        assert o and o.tipo == "parar", frase


def test_pregunta_normal_va_a_claude():
    assert interpretar("¿Qué archivo apunta las tareas pendientes?", PROYECTOS) is None
    assert interpretar("", PROYECTOS) is None
    assert interpretar("para qué sirve el proyecto de la web y qué falta por hacer", PROYECTOS) is None


def test_cambia_el_proyecto():
    o = interpretar("Cambia el proyecto Movicio", PROYECTOS)
    assert o and o.tipo == "proyecto" and o.proyecto == "movicio"


def test_whisper_real_y_formas_sueltas():
    # Transcripciones reales del journal (21/11) y formas habladas.
    for frase in (
        "Quiero que cambia esa movición. Proyecto movición.",
        "Cambia el proyecto movición",
        "cambia a Movicio",
        "Vamos al proyecto de Movicio",
        "Pásate al proyecto mobicio, por favor",
        "ponme el proyecto de las consolas",
        "ve a movicio",
    ):
        o = interpretar(frase, PROYECTOS)
        assert o and o.tipo == "proyecto" and o.proyecto == "movicio", frase
    o = interpretar("vuelve al proyecto minibestia", PROYECTOS)
    assert o and o.proyecto == "minibestia"
    o = interpretar("cambia al proyecto de la web", PROYECTOS)
    assert o and o.proyecto == "minibestia"


def test_no_confunde_preguntas_con_cambios():
    for frase in (
        "¿Qué pasa con el proyecto Movicio?",
        "cambia el título de la guía de la web",
        "¿Qué falta en el proyecto movicio?",
        "pasa a limpio lo que hablamos ayer del proyecto y dime qué falta por hacer en movicio",
        "abre el navegador y ponme un vídeo de consolas",
    ):
        assert interpretar(frase, PROYECTOS) is None, frase


def test_listar_sesiones():
    for frase in ("Lista las sesiones", "¿Qué sesiones hay?", "Dime las sesiones", "muestra las sesiones por favor"):
        o = interpretar(frase, PROYECTOS)
        assert o and o.tipo == "sesiones", frase


def test_ir_a_sesion():
    o = interpretar("Ve a la sesión dos", PROYECTOS)
    assert o and o.tipo == "sesion" and o.pedido == "dos"
    o = interpretar("Cambia a la sesión del asistente de voz", PROYECTOS)
    assert o and o.tipo == "sesion" and o.pedido == "del asistente de voz"
    o = interpretar("retoma la sesión tres", PROYECTOS)
    assert o and o.tipo == "sesion" and o.pedido == "tres"
    o = interpretar("ve a la sesión", PROYECTOS)
    assert o and o.tipo == "sesiones"


def test_sesion_no_confunde_con_proyecto():
    o = interpretar("cambia al proyecto movicio", PROYECTOS)
    assert o and o.tipo == "proyecto"
    o = interpretar("ve a la sesión dos", PROYECTOS)
    assert o and o.tipo == "sesion"


def test_sesiones_en_preguntas_normales():
    assert interpretar("¿cuántas sesiones de entrenamiento lleva el modelo?", PROYECTOS) is None


def test_confirmar_solo_con_pendiente():
    for frase in ("Sí", "sí, hazlo", "Adelante", "vale", "De acuerdo.", "sí por favor", "sí, claro", "ok"):
        o = interpretar(frase, PROYECTOS, pendiente=True)
        assert o and o.tipo == "confirmar", frase
        sin_pendiente = interpretar(frase, PROYECTOS, pendiente=False)
        assert sin_pendiente is None or sin_pendiente.tipo == "parar", frase  # «cancela» también manda callar


def test_negar_solo_con_pendiente():
    for frase in ("No", "cancela", "mejor no", "No lo hagas", "no, déjalo estar"):
        o = interpretar(frase, PROYECTOS, pendiente=True)
        assert o and o.tipo == "negar", frase
        sin_pendiente = interpretar(frase, PROYECTOS, pendiente=False)
        assert sin_pendiente is None or sin_pendiente.tipo == "parar", frase  # «cancela» también manda callar


def test_confirmar_no_atrapa_frases_largas():
    assert interpretar("sí, pero antes dime qué hora es", PROYECTOS, pendiente=True) is None
    assert interpretar("si no lo hagas", PROYECTOS, pendiente=True) is None


def test_parar_formas_habladas():
    for frase in ("Para ya", "Stop.", "cállate ya", "Deja de hablar", "ya vale"):
        o = interpretar(frase, PROYECTOS)
        assert o and o.tipo == "parar", frase
