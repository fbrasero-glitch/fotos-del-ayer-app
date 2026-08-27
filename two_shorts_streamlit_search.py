from __future__ import annotations

import sqlite3

from streamlit.testing.v1 import AppTest


DB_PATH = "data/fotos_de_ayer.db"

SEARCHES = {
    6: {
        33: "young Elvis Presley emotional portrait close up black and white 1950s",
        37: "Elvis Presley childhood Tupelo first guitar 1946 family",
        38: "Elvis Presley Ed Sullivan television performance 1956",
        39: "Elvis Presley mother Gladys at Graceland 1957",
        40: "Elvis Presley 1968 comeback special black leather",
        41: "Elvis Presley Las Vegas final years 1977 stage",
        34: "Elvis Presley reflective portrait alone 1970s",
    },
    7: {
        35: "Audrey Hepburn emotional portrait eyes close up black and white",
        42: "Audrey Hepburn childhood Netherlands World War II 1944",
        43: "Audrey Hepburn Roman Holiday Academy Award Oscar 1954",
        44: "Audrey Hepburn Breakfast at Tiffany's iconic portrait 1961",
        45: "Audrey Hepburn sons Sean Luca family Switzerland",
        46: "Audrey Hepburn UNICEF Ethiopia children 1988",
        36: "Audrey Hepburn UNICEF later years compassionate portrait",
    },
}


def element(elements, label):
    return next(item for item in elements if item.label == label)


def has_search(slot_id: int) -> bool:
    with sqlite3.connect(DB_PATH) as connection:
        row = connection.execute(
            "SELECT 1 FROM production_slot_searches WHERE slot_id=? LIMIT 1",
            (slot_id,),
        ).fetchone()
    return row is not None


def main() -> None:
    app = AppTest.from_file("app_projects.py").run(timeout=30)
    for project_id, slots in SEARCHES.items():
        element(app.selectbox, "Proyecto activo").set_value(project_id)
        app.run(timeout=30)
        for slot_id, query in slots.items():
            if has_search(slot_id):
                print(f"SKIP slot={slot_id} history=1", flush=True)
                continue
            element(app.selectbox, "Búsqueda abierta").set_value(slot_id)
            app.run(timeout=30)
            element(app.text_input, "Palabras clave en inglés").set_value(query)
            element(app.button, "Buscar según mi método").click()
            app.run(timeout=150)
            if app.exception:
                raise RuntimeError(" | ".join(str(exc.message) for exc in app.exception))
            notices = [str(item.value) for item in app.success]
            print(f"DONE project={project_id} slot={slot_id} notices={notices}", flush=True)


if __name__ == "__main__":
    main()
