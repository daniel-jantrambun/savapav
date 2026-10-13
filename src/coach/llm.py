"""Rédaction des séances par un LLM 3B local (Ollama ou llama.cpp server, API compatible OpenAI).

Le LLM reçoit une semaine du plan en JSON et renvoie du texte : il ne calcule rien et ne modifie aucun
chiffre. On l'appelle semaine par semaine : sur le J4125 (sans AVX), l'évaluation d'un long prompt est
le vrai goulot, bien plus que la génération.
"""

import json
import logging
import time

import httpx

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "Tu es un coach de course à pied. On te donne une semaine d'entraînement en JSON, déjà calculée. "
    "Rédige en français, pour chaque séance, 1 à 2 phrases claires : contenu, allure en min/km, objectif. "
    "N'invente aucune séance et ne change aucun chiffre (distance, allure, répétitions). "
    "Termine par une phrase sur l'objectif de la semaine (phase, décharge éventuelle)."
)

MULTISPORT_PROMPT = (
    "Tu es un coach multisport (course à pied, vélo, natation, renforcement en salle). On te donne une semaine "
    "d'entraînement en JSON, déjà calculée. Rédige en français, pour chaque séance, 1 à 2 phrases claires : contenu, "
    "intensité (allure ou fréquence cardiaque indiquée), objectif. Pour la salle, décris le type de séance et sa "
    "durée, avec au plus trois exemples d'exercices, sans séries ni répétitions. N'invente aucune séance et ne "
    "change aucun chiffre (durée, distance, allure, fréquence cardiaque, répétitions). "
    "Termine par une phrase sur l'objectif de la semaine (charge ou décharge)."
)

SPORT_FR = {"run": "course à pied", "bike": "vélo", "swim": "natation", "gym": "salle"}
KIND_FR = {
    "easy": "endurance facile", "long": "sortie longue", "tempo": "seuil", "intervals": "fractionné",
    "strides": "footing + lignes droites", "technique": "technique", "strength": "renforcement",
}
FOCUS_FR = {"lower": "bas du corps", "upper": "haut du corps", "full_body": "corps entier",
            "core": "gainage et mobilité"}


def _format_pace(seconds_per_km: int) -> str:
    return f"{seconds_per_km // 60}:{seconds_per_km % 60:02d}/km"


def _format_swim_pace(seconds_per_100m: int) -> str:
    return f"{seconds_per_100m // 60}:{seconds_per_100m % 60:02d}/100m"


def _repetitions(s: dict) -> str:
    effort = f"{s['rep_m']}m" if s["rep_m"] else f"{s['rep_s'] // 60}min"
    return f"{s['reps']}x{effort}, récup {s['recovery_s']}s"


def multisport_week_prompt(week: dict) -> str:
    sessions = []
    for s in week["sessions"]:
        item = {"date": str(s["date"]), "sport": SPORT_FR[s["sport"]], "type": KIND_FR[s["kind"]],
                "duree_min": s["duration_min"]}
        if s["focus"]:
            item["zone"] = FOCUS_FR[s["focus"]]
        if s["distance_km"]:
            item["distance"] = f"{round(s['distance_km'] * 1000)}m" if s["sport"] == "swim" else f"{s['distance_km']}km"
        if s["pace_s_per_km"]:
            item["allure"] = _format_pace(s["pace_s_per_km"])
        if s["pace_s_per_100m"]:
            item["allure"] = _format_swim_pace(s["pace_s_per_100m"])
        if s["hr_low"]:
            item["fc"] = f"{s['hr_low']}-{s['hr_high']} bpm"
        if s["reps"]:
            item["repetitions"] = _repetitions(s)
        sessions.append(item)
    compact = {"semaine": week["index"] + 1, "decharge": week["deload"], "duree_totale_min": week["minutes"],
               "seances": sessions}
    return json.dumps(compact, ensure_ascii=False)


def week_prompt(week: dict) -> str:
    compact = {
        "semaine": week["index"] + 1,
        "phase": week["phase"],
        "decharge": week["deload"],
        "volume_km": week["volume_km"],
        "seances": [
            {
                "date": str(s["date"]),
                "type": s["kind"],
                "distance_km": s["distance_km"],
                "allure": _format_pace(s["pace_s_per_km"]),
                **({"repetitions": f"{s['reps']}x{s['rep_m']}m, récup {s['recovery_s']}s"} if s["reps"] else {}),
            }
            for s in week["sessions"]
        ],
    }
    return json.dumps(compact, ensure_ascii=False)


def narrate_week(week: dict, base_url: str, model: str, timeout_s: float, multisport: bool = False) -> str:
    system = MULTISPORT_PROMPT if multisport else SYSTEM_PROMPT
    user = multisport_week_prompt(week) if multisport else week_prompt(week)
    # Prompt et réponse dans les logs du serveur, pour ajuster les consignes au vu de ce que rédige le modèle.
    log.info("LLM %s, semaine %s — prompt système : %s", model, week["index"] + 1, system)
    log.info("LLM %s, semaine %s — prompt utilisateur : %s", model, week["index"] + 1, user)
    started = time.monotonic()
    response = httpx.post(
        f"{base_url.rstrip('/')}/v1/chat/completions",
        json={
            "model": model,
            "temperature": 0.3,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        },
        timeout=timeout_s,
    )
    response.raise_for_status()
    text = response.json()["choices"][0]["message"]["content"].strip()
    log.info("LLM %s, semaine %s — réponse en %.0f s : %s", model, week["index"] + 1, time.monotonic() - started, text)
    return text
