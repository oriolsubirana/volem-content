#!/usr/bin/env python3
"""Validador de contenido de Volem.

Valida los packs de cards (bundled y de CDN) y el manifest contra las
invariantes que el motor asume. Corre en CI (job de Ubuntu) y en local:

    python3 Tools/validate_packs.py

Sale con código != 0 si hay violaciones.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PACK_DIRS = [
    REPO,
]
_UNUSED = [
    REPO / "Volem" / "Content" / "Bundled",   # embebidos en la app
    REPO / "content",                          # carpeta lista para el CDN
]
MANIFEST_DIR = REPO

CARD_TYPES = {"cover", "story", "tip", "myth", "quiz", "slider", "final"}
VERDICTS = {"MITO", "REALIDAD"}

errors: list[str] = []


def err(path: Path, message: str) -> None:
    errors.append(f"{path.relative_to(REPO)}: {message}")


def require(card: dict, path: Path, cid: str, *fields: str) -> bool:
    ok = True
    for field in fields:
        value = card.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            err(path, f"card {cid}: falta el campo '{field}' o está vacío")
            ok = False
    return ok


def validate_card(card: dict, path: Path, destination_id: str) -> None:
    cid = card.get("id", "<sin id>")
    ctype = card.get("type")

    if ctype not in CARD_TYPES:
        err(path, f"card {cid}: tipo desconocido '{ctype}'")
        return
    if not str(cid).startswith(f"{destination_id}-"):
        err(path, f"card {cid}: el id no lleva el prefijo del destino '{destination_id}-'")

    if ctype == "cover":
        require(card, path, cid, "kicker", "title", "sub")
    elif ctype in ("story", "tip"):
        require(card, path, cid, "kicker", "title", "body")
    elif ctype == "myth":
        if require(card, path, cid, "kicker", "claim", "verdict", "body"):
            if card["verdict"] not in VERDICTS:
                err(path, f"card {cid}: verdict '{card['verdict']}' no es MITO/REALIDAD")
    elif ctype == "quiz":
        if require(card, path, cid, "kicker", "question", "explain"):
            options = card.get("options")
            answer = card.get("answer")
            if not isinstance(options, list) or len(options) < 2:
                err(path, f"card {cid}: quiz necesita al menos 2 opciones")
            elif not isinstance(answer, int) or not 0 <= answer < len(options):
                err(path, f"card {cid}: answer {answer} fuera del rango de opciones")
    elif ctype == "slider":
        if require(card, path, cid, "kicker", "question", "explain_correct", "explain_wrong"):
            lo, hi = card.get("min"), card.get("max")
            answer, tolerance = card.get("answer"), card.get("tolerance")
            if not all(isinstance(v, int) for v in (lo, hi, answer, tolerance)):
                err(path, f"card {cid}: min/max/answer/tolerance deben ser enteros")
            else:
                if lo >= hi:
                    err(path, f"card {cid}: rango inválido ({lo} >= {hi})")
                if not lo <= answer <= hi:
                    err(path, f"card {cid}: answer {answer} fuera del rango [{lo}, {hi}]")
                if tolerance <= 0 or tolerance >= (hi - lo):
                    err(path, f"card {cid}: tolerance {tolerance} sin sentido para el rango")


def validate_pack(path: Path) -> dict | None:
    try:
        pack = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        err(path, f"JSON inválido: {exc}")
        return None

    missing = [
        field
        for field in ("schemaVersion", "destinationID", "destination", "country",
                      "language", "version", "estimated_minutes", "cards")
        if field not in pack
    ]
    for field in missing:
        err(path, f"falta el campo '{field}' del pack")
    # Sin estos dos no se puede validar nada más; el resto de campos que
    # falten no impiden seguir buscando problemas en las cards.
    if "destinationID" in missing or "cards" in missing:
        return None

    destination_id = pack.get("destinationID", "")
    cards = pack.get("cards", [])
    if not isinstance(cards, list) or len(cards) < 3:
        err(path, "el pack necesita al menos cover + 1 card + final")
        return pack

    ids = [c.get("id") for c in cards]
    duplicated = {i for i in ids if ids.count(i) > 1}
    if duplicated:
        err(path, f"IDs duplicados: {sorted(duplicated, key=str)} — el progreso por ID estable lo prohíbe")

    if cards[0].get("type") != "cover":
        err(path, "la primera card debe ser cover")
    if cards[-1].get("type") != "final":
        err(path, "la última card debe ser final")
    for mid in cards[1:-1]:
        if mid.get("type") in ("cover", "final"):
            err(path, f"card {mid.get('id')}: cover/final solo pueden ir en los extremos")

    if pack.get("version", 0) < 1:
        err(path, "version del pack debe ser >= 1")
    if pack.get("schemaVersion") != 2:
        err(path, f"schemaVersion {pack.get('schemaVersion')} != 2 (el que entiende la app)")

    for card in cards:
        validate_card(card, path, destination_id)

    return pack


def validate_manifest(path: Path, language: str,
                      packs: dict[tuple[str, str], tuple[Path, dict]]) -> None:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        err(path, f"JSON inválido: {exc}")
        return

    destinations = manifest.get("destinations")
    if not isinstance(destinations, list) or not destinations:
        err(path, "falta la lista 'destinations'")
        return
    if "updatedAt" not in manifest:
        err(path, "falta 'updatedAt'")

    seen: set[str] = set()
    for dest in destinations:
        did = dest.get("id", "<sin id>")
        if did in seen:
            err(path, f"destino duplicado '{did}'")
        seen.add(did)

        for field in ("name", "country", "latitude", "longitude", "iataCodes",
                      "packVersion", "packSizeBytes", "cardCount", "estimatedMinutes"):
            if field not in dest:
                err(path, f"destino {did}: falta '{field}'")

        # Si el pack del destino (en el idioma del manifest) vive en el repo,
        # el manifest debe cuadrar con él.
        if (did, language) in packs:
            pack_path, pack = packs[(did, language)]
            if dest.get("cardCount") != len(pack["cards"]):
                err(path, f"destino {did}: cardCount {dest.get('cardCount')} "
                          f"!= {len(pack['cards'])} cards reales en {pack_path.name}")
            if dest.get("packVersion") != pack.get("version"):
                err(path, f"destino {did}: packVersion {dest.get('packVersion')} "
                          f"!= version {pack.get('version')} del pack")
            actual_size = pack_path.stat().st_size
            declared = dest.get("packSizeBytes", 0)
            if abs(declared - actual_size) > actual_size * 0.25:
                err(path, f"destino {did}: packSizeBytes {declared} muy lejos "
                          f"del tamaño real {actual_size}")
            if dest.get("estimatedMinutes") != pack.get("estimated_minutes"):
                err(path, f"destino {did}: estimatedMinutes {dest.get('estimatedMinutes')} "
                          f"!= estimated_minutes {pack.get('estimated_minutes')} del pack")
            pack_url = dest.get("packURL")
            if pack_url and not str(pack_url).endswith(pack_path.name):
                err(path, f"destino {did}: packURL '{pack_url}' no apunta al "
                          f"fichero del pack en este idioma ({pack_path.name})")


def card_signature(card: dict) -> tuple:
    """Lo que debe ser idéntico entre idiomas: identidad, tipo y semántica
    de juego. Los textos se traducen; las respuestas no."""
    sig: list = [card.get("id"), card.get("type")]
    ctype = card.get("type")
    if ctype == "quiz":
        sig += [len(card.get("options") or []), card.get("answer")]
    elif ctype == "slider":
        sig += [card.get("min"), card.get("max"), card.get("answer"), card.get("tolerance")]
    elif ctype == "myth":
        sig += [card.get("verdict")]
    return tuple(sig)


def validate_translations(packs: dict[tuple[str, str], tuple[Path, dict]]) -> None:
    """Regla de oro multi-idioma: el progreso se guarda por card ID, así que
    todas las traducciones de un pack deben tener los MISMOS IDs, en el mismo
    orden, con los mismos tipos y con la misma semántica de juego (índice de
    respuesta, rango del slider, verdict): traducir no puede cambiar qué
    respuesta es la correcta."""
    by_destination: dict[str, list[tuple[str, Path, dict]]] = {}
    for (did, language), (path, pack) in packs.items():
        by_destination.setdefault(did, []).append((language, path, pack))

    for did, variants in by_destination.items():
        if len(variants) < 2:
            continue
        reference_lang, reference_path, reference = sorted(variants)[0]
        ref_shape = [card_signature(c) for c in reference["cards"]]
        for language, path, pack in sorted(variants)[1:]:
            shape = [card_signature(c) for c in pack["cards"]]
            for ref_sig, sig in zip(ref_shape, shape):
                if ref_sig != sig:
                    err(path, f"pack {did}/{language}: la card {sig[0]} no coincide con "
                              f"{reference_path.name} ({ref_sig} != {sig}) — "
                              f"traducir no puede cambiar IDs, tipos ni respuestas")
            if len(shape) != len(ref_shape):
                err(path, f"pack {did}/{language}: {len(shape)} cards != "
                          f"{len(ref_shape)} en {reference_path.name}")


def main() -> int:
    packs: dict[tuple[str, str], tuple[Path, dict]] = {}
    pack_files = sorted(
        p for d in PACK_DIRS if d.exists()
        for p in d.glob("*.json") if not p.name.startswith("manifest")
    )
    if not pack_files:
        print("No se encontró ningún pack que validar", file=sys.stderr)
        return 1

    for path in pack_files:
        pack = validate_pack(path)
        if pack and pack.get("destinationID"):
            key = (pack["destinationID"], pack.get("language", "es"))
            if key in packs:
                err(path, f"pack duplicado para {key}")
            packs[key] = (path, pack)

    validate_translations(packs)

    manifest_files = sorted(MANIFEST_DIR.glob("manifest*.json")) if MANIFEST_DIR.exists() else []
    for path in manifest_files:
        language = "es" if path.name == "manifest.json" else path.stem.split("-", 1)[1]
        validate_manifest(path, language, packs)

    if errors:
        print(f"✗ {len(errors)} problema(s) de contenido:\n")
        for e in errors:
            print(f"  - {e}")
        return 1

    for path in pack_files:
        pack = json.loads(path.read_text(encoding="utf-8"))
        print(f"✓ {path.relative_to(REPO)} — {len(pack['cards'])} cards "
              f"({pack['destination']}, {pack.get('language', 'es')})")
    for path in manifest_files:
        print(f"✓ {path.relative_to(REPO)} — coherente con los packs")
    print("\nContenido válido.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
