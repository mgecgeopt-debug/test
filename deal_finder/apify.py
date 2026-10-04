"""Apify-API: Task starten, auf Ergebnis warten, Dataset holen. Kein Webhook."""
import logging
import time

import requests

log = logging.getLogger(__name__)
API = "https://api.apify.com/v2"
FERTIG = {"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"}
ACTOR = "lexis-solutions~ebay-kleinanzeigen"


class ApifyFehler(RuntimeError):
    pass


class Apify:
    def __init__(self, token: str, api_basis: str = API, timeout: int = 150):
        self._kopf = {"Authorization": f"Bearer {token}"}
        self._basis = api_basis
        self.timeout = timeout

    def starte_task(self, task_id: str, eingabe: dict | None = None, wait_for_finish: int = 120) -> dict:
        """POST /actor-tasks/{id}/runs. `eingabe` überschreibt Felder des Task-Inputs."""
        r = requests.post(
            f"{self._basis}/actor-tasks/{task_id}/runs",
            params={"waitForFinish": wait_for_finish},
            json=eingabe or {},
            headers=self._kopf,
            timeout=self.timeout,
        )
        if r.status_code >= 400:
            raise ApifyFehler(f"Task {task_id} starten: HTTP {r.status_code} {r.text[:200]}")
        return r.json()["data"]

    def warte_auf_run(self, run_id: str, max_sekunden: int = 600, abstand: int = 10) -> dict:
        """Fragt den Run-Status ab, bis er fertig ist (für Läufe länger als waitForFinish)."""
        ende = time.monotonic() + max_sekunden
        while True:
            r = requests.get(f"{self._basis}/actor-runs/{run_id}", headers=self._kopf, timeout=self.timeout)
            r.raise_for_status()
            run = r.json()["data"]
            if run["status"] in FERTIG:
                return run
            if time.monotonic() > ende:
                raise ApifyFehler(f"Run {run_id} nach {max_sekunden}s noch {run['status']}")
            time.sleep(abstand)

    def hole_items(self, dataset_id: str) -> list[dict]:
        """GET /datasets/{id}/items?clean=true, seitenweise."""
        items: list[dict] = []
        offset = 0
        while True:
            r = requests.get(
                f"{self._basis}/datasets/{dataset_id}/items",
                params={"clean": "true", "offset": offset, "limit": 1000},
                headers=self._kopf,
                timeout=self.timeout,
            )
            r.raise_for_status()
            seite = r.json()
            items.extend(seite)
            if len(seite) < 1000:
                return items
            offset += 1000

    def finde_oder_erstelle_task(self, query: str, max_items: int = 50) -> str:
        """Sucht eine Task 'deal-finder-<query>' zum Kleinanzeigen-Actor, legt sie sonst an (1 GB)."""
        name = "deal-finder-" + "".join(c if c.isalnum() else "-" for c in query.lower()).strip("-")
        r = requests.get(f"{self._basis}/actor-tasks", params={"limit": 1000}, headers=self._kopf, timeout=self.timeout)
        r.raise_for_status()
        for t in r.json()["data"]["items"]:
            if t["name"] == name:
                return t["id"]
        akt = requests.get(f"{self._basis}/acts/{ACTOR}", headers=self._kopf, timeout=self.timeout)
        akt.raise_for_status()
        body = {
            "actId": akt.json()["data"]["id"], "name": name,
            "options": {"memoryMbytes": 1024, "timeoutSecs": 600},
            "input": {"query": query, "maxItems": max_items, "monitoringMode": True,
                      "monitoringFields": ["price", "title"], "fetchViewsCount": False,
                      "proxy": {"useApifyProxy": True}},
        }
        r = requests.post(f"{self._basis}/actor-tasks", headers=self._kopf, json=body, timeout=self.timeout)
        if r.status_code >= 400:
            raise ApifyFehler(f"Task {name} anlegen: HTTP {r.status_code} {r.text[:200]}")
        task_id = r.json()["data"]["id"]
        log.info("Apify-Task '%s' angelegt: %s", name, task_id)
        return task_id

    def lauf(self, task_id: str, eingabe: dict | None = None, wait_for_finish: int = 120) -> list[dict]:
        """Kompletter Durchgang: starten, warten, Items holen. Wirft ApifyFehler bei Misserfolg."""
        run = self.starte_task(task_id, eingabe, wait_for_finish)
        if run["status"] not in FERTIG:
            run = self.warte_auf_run(run["id"])
        if run["status"] != "SUCCEEDED":
            raise ApifyFehler(f"Task {task_id}: Run {run['id']} endete mit {run['status']}")
        items = self.hole_items(run["defaultDatasetId"])
        log.info("Task %s: %d Items (Run %s)", task_id, len(items), run["id"])
        return items
