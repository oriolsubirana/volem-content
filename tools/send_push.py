#!/usr/bin/env python3
"""Manda avisos push a los móviles registrados en Supabase, hablando con
APNs directamente. Sin servidor propio: esto corre en una GitHub Action.

    python3 tools/send_push.py --from-json novedades.json [--dry-run]
    python3 tools/send_push.py --title-es ... --body-es ... --title-en ... --body-en ... \
                               [--destination lis] [--dry-run]

Entorno:
    SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY   (lee y poda tokens)
    APNS_KEY_P8, APNS_KEY_ID, APNS_TEAM_ID    (clave de APNs de la cuenta)
    APNS_TOPIC                                (bundle id; por defecto oriol.subirana.Volem)

Qué hace con cada token: lo manda al servidor de APNs que le toque (sandbox
para builds de Xcode, producción para TestFlight y App Store), en su idioma.
Si Apple contesta que el token ya no existe, lo borra de Supabase. Si un
dispositivo tiene varios tokens (APNs los rota), se queda con el más nuevo.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time

APNS_HOSTS = {
    "production": "https://api.push.apple.com",
    "sandbox": "https://api.sandbox.push.apple.com",
}
DEAD_REASONS = {"BadDeviceToken", "Unregistered", "DeviceTokenNotForTopic"}
FATAL_REASONS = {"BadTopic", "InvalidProviderToken", "MissingProviderToken",
                 "ExpiredProviderToken", "TopicDisallowed", "Forbidden"}


# --- Textos ------------------------------------------------------------------

def compose(entry: dict) -> dict:
    """Los textos de un aviso a partir de una entrada de new_packs.py."""
    es, en = entry["name_es"], entry["name_en"]
    if entry.get("new_destination"):
        return {
            "destination": entry["destination"],
            "es": (f"Nuevo destino: {es}", f"Ya puedes preparar el vuelo a {es}."),
            "en": (f"New destination: {en}", f"You can start getting ready for {en}."),
        }
    packs = entry["packs"]
    titles_es = " · ".join(p["title_es"] for p in packs)
    titles_en = " · ".join(p["title_en"] for p in packs)
    if len(packs) == 1:
        return {
            "destination": entry["destination"],
            "es": (f"{es} tiene un pack nuevo", titles_es),
            "en": (f"{en} has a new pack", titles_en),
        }
    return {
        "destination": entry["destination"],
        "es": (f"{es} tiene {len(packs)} packs nuevos", titles_es),
        "en": (f"{en} has {len(packs)} new packs", titles_en),
    }


# --- APNs --------------------------------------------------------------------

def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def apns_jwt(p8: str, key_id: str, team_id: str, now: int | None = None) -> str:
    """Token de proveedor ES256, firmado con la clave .p8. La firma va en
    crudo (r‖s, 64 bytes), no en DER: es lo que Apple espera."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    key = serialization.load_pem_private_key(p8.encode(), password=None)
    header = b64url(json.dumps({"alg": "ES256", "kid": key_id}).encode())
    claims = b64url(json.dumps({"iss": team_id, "iat": now or int(time.time())}).encode())
    signing_input = f"{header}.{claims}".encode()
    r, s = decode_dss_signature(key.sign(signing_input, ec.ECDSA(hashes.SHA256())))
    return f"{header}.{claims}.{b64url(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


def payload(title: str, body: str, destination: str | None) -> dict:
    data = {"aps": {"alert": {"title": title, "body": body}, "sound": "default"}}
    if destination:
        data["destination"] = destination
    return data


# --- Supabase ----------------------------------------------------------------

class TokenStore:
    def __init__(self, url: str, service_key: str, client) -> None:
        self.base = f"{url.rstrip('/')}/rest/v1/push_token"
        self.headers = {"apikey": service_key, "Authorization": f"Bearer {service_key}"}
        self.client = client

    def fetch(self) -> list[dict]:
        r = self.client.get(self.base, headers=self.headers, params={
            "select": "token,installation_id,language,environment,updated_at",
            "order": "updated_at.desc",
        })
        r.raise_for_status()
        return r.json()

    def delete(self, token: str) -> None:
        r = self.client.delete(self.base, headers=self.headers, params={"token": f"eq.{token}"})
        r.raise_for_status()


def prune(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Un token por instalación: el más reciente (vienen ordenados). El
    resto son rotaciones viejas que APNs ya no reconoce."""
    keep, stale, seen = [], [], set()
    for row in rows:
        key = row.get("installation_id") or row["token"]
        if key in seen:
            stale.append(row)
        else:
            seen.add(key)
            keep.append(row)
    return keep, stale


# --- Envío -------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from-json", help="salida de new_packs.py")
    parser.add_argument("--title-es"); parser.add_argument("--body-es")
    parser.add_argument("--title-en"); parser.add_argument("--body-en")
    parser.add_argument("--destination", default=None)
    parser.add_argument("--dry-run", action="store_true", help="no manda nada, cuenta y enseña los textos")
    args = parser.parse_args()

    if args.from_json:
        with open(args.from_json, encoding="utf-8") as f:
            entries = json.load(f)
        notices = [compose(e) for e in entries]
    elif all([args.title_es, args.body_es, args.title_en, args.body_en]):
        notices = [{"destination": args.destination,
                    "es": (args.title_es, args.body_es), "en": (args.title_en, args.body_en)}]
    else:
        parser.error("hace falta --from-json o los cuatro textos")

    if not notices:
        print("Nada que avisar.")
        return 0

    for n in notices:
        print(f"· {n['es'][0]} — {n['es'][1]}")
        print(f"  {n['en'][0]} — {n['en'][1]}")

    env = {k: os.environ.get(k, "") for k in
           ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "APNS_KEY_P8", "APNS_KEY_ID", "APNS_TEAM_ID")}
    missing = [k for k, v in env.items() if not v]
    if missing:
        print(f"Faltan variables: {', '.join(missing)}", file=sys.stderr)
        return 1
    topic = os.environ.get("APNS_TOPIC", "oriol.subirana.Volem")

    import httpx
    with httpx.Client(http2=True, timeout=20) as client:
        store = TokenStore(env["SUPABASE_URL"], env["SUPABASE_SERVICE_ROLE_KEY"], client)
        rows, stale = prune(store.fetch())
        by_bucket: dict[tuple[str, str], int] = {}
        for row in rows:
            k = (row.get("environment") or "production", (row.get("language") or "en")[:2])
            by_bucket[k] = by_bucket.get(k, 0) + 1
        print(f"\n{len(rows)} dispositivos ({len(stale)} tokens viejos a podar):")
        for (environment, language), n in sorted(by_bucket.items()):
            print(f"  {n:4}  {environment:10} {language}")
        if args.dry_run:
            print("\n--dry-run: no se ha mandado nada.")
            return 0

        for row in stale:
            store.delete(row["token"])

        jwt = apns_jwt(env["APNS_KEY_P8"], env["APNS_KEY_ID"], env["APNS_TEAM_ID"])
        headers = {"authorization": f"bearer {jwt}", "apns-topic": topic,
                   "apns-push-type": "alert", "apns-priority": "10",
                   "apns-expiration": str(int(time.time()) + 86400)}
        sent = dead = failed = 0
        for notice in notices:
            for row in rows:
                environment = row.get("environment") or "production"
                language = "es" if (row.get("language") or "en").startswith("es") else "en"
                title, body = notice[language]
                host = APNS_HOSTS.get(environment, APNS_HOSTS["production"])
                r = client.post(f"{host}/3/device/{row['token']}", headers=headers,
                                json=payload(title, body, notice["destination"]))
                if r.status_code == 200:
                    sent += 1
                    continue
                reason = ""
                try:
                    reason = r.json().get("reason", "")
                except ValueError:
                    pass
                if r.status_code == 410 or reason in DEAD_REASONS:
                    store.delete(row["token"])
                    dead += 1
                elif reason in FATAL_REASONS:
                    print(f"\nAPNs rechaza la configuración: {r.status_code} {reason}", file=sys.stderr)
                    return 1
                else:
                    failed += 1
                    print(f"  ✗ {r.status_code} {reason} ({environment}/{language})")

    print(f"\nEnviados {sent} · tokens muertos borrados {dead} · fallidos {failed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
