#!/usr/bin/env python3
"""Qué ha aparecido en el manifest entre dos revisiones: destinos que pasan
a ser jugables y packs temáticos nuevos en destinos que ya lo eran.

    python3 tools/new_packs.py <base-rev> <head-rev>

Escribe JSON en stdout, una entrada por destino con novedades. Es la entrada
de send_push.py. Si la revisión base no existe (primer push) no hay con qué
comparar: escribe [] y avisa, en vez de anunciar el catálogo entero.
"""

from __future__ import annotations

import json
import subprocess
import sys


def manifest_at(rev: str, name: str) -> dict | None:
    try:
        out = subprocess.run(["git", "show", f"{rev}:{name}"],
                             capture_output=True, text=True, check=True).stdout
    except subprocess.CalledProcessError:
        return None
    return json.loads(out)


def packs_of(dest: dict | None) -> dict[str, str | None]:
    """Los packs jugables de un destino como {id: título}. Sin `packs`, el
    general se sintetiza igual que hace la app."""
    if not dest:
        return {}
    declared = dest.get("packs")
    if declared:
        return {p["id"]: p.get("title") for p in declared}
    if dest.get("packURL"):
        return {"main": None}
    return {}


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    base, head = sys.argv[1], sys.argv[2]

    head_es = manifest_at(head, "manifest.json")
    head_en = manifest_at(head, "manifest-en.json") or {"destinations": []}
    base_es = manifest_at(base, "manifest.json") if not set(base) <= {"0"} else None
    if head_es is None:
        print("No hay manifest.json en la revisión de cabecera", file=sys.stderr)
        return 1
    if base_es is None:
        print(f"::warning::Sin revisión base ({base}): no se anuncia nada", file=sys.stderr)
        print("[]")
        return 0

    by_id_en = {d["id"]: d for d in head_en["destinations"]}
    by_id_base = {d["id"]: d for d in base_es["destinations"]}

    news = []
    for dest in head_es["destinations"]:
        before = packs_of(by_id_base.get(dest["id"]))
        after = packs_of(dest)
        en = by_id_en.get(dest["id"], {})
        entry = {
            "destination": dest["id"],
            "name_es": dest["name"],
            "name_en": en.get("name", dest["name"]),
        }
        if after and not before:
            news.append({**entry, "new_destination": True, "packs": []})
            continue
        new_ids = [pid for pid in after if pid not in before and pid != "main"]
        if not new_ids:
            continue
        en_titles = {p["id"]: p.get("title") for p in en.get("packs") or []}
        news.append({
            **entry,
            "new_destination": False,
            "packs": [{"id": pid, "title_es": after[pid], "title_en": en_titles.get(pid, after[pid])}
                      for pid in new_ids],
        })

    print(json.dumps(news, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
