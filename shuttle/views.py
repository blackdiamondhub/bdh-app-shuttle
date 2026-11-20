import json
import logging

from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_http_methods

from . import config, live, reminders, schedule, stops, worker

log = logging.getLogger(__name__)


def index(request):
    now = schedule.local_now()
    day = schedule.departures(schedule.config.season_start())
    lodge = schedule.board(schedule.LODGE, now)
    return render(request, "shuttle/index.html", {
        "next": schedule.next_departure(now),
        "in_season": schedule.in_season(now),
        "season_note": schedule.season_note(now),
        "first_bus": schedule.label(day[0]),
        "last_bus": schedule.label(day[-1]),
        "lodge": lodge,
        "boards": [(schedule.LODGE, lodge),
                   (schedule.VILLAGE, schedule.board(schedule.VILLAGE, now))],
        "reminders_on": reminders.enabled(),
        "leads": schedule.LEADS,
        "geo": {"stops": stops.STOPS, "route": stops.route(), "our": stops.OUR_STOP},
    })


@require_GET
def live_view(request):
    return JsonResponse(live.status())


def _reminder_list():
    return JsonResponse({"reminders": reminders.pending()})


def _speaker_choices():
    """Allowed speakers as ``[[entity_id, label], ...]``, labels from the hub when it answers."""
    allowed = config.speakers()
    try:
        from bdh_sdk import speakers as sp
        labels = dict(sp.list_speakers())
    except Exception:  # noqa: BLE001 - HA or the broker unreachable: fall back to ids
        log.warning("listing speakers failed", exc_info=True)
        labels = {}
    return [[eid, labels.get(eid, eid.split(".", 1)[-1].replace("_", " ").title())] for eid in allowed]


@require_GET
def speakers_view(request):
    if not reminders.enabled():
        return JsonResponse({"error": "Reminders aren't set up here."}, status=404)
    return JsonResponse({"speakers": _speaker_choices()})


@require_GET
def popup(request, minutes):
    return render(request, "shuttle/popup.html", {
        "minutes": reminders.minutes_phrase(max(1, minutes)),
        "stop": reminders.STOP, "destination": reminders.DESTINATION,
    })


@require_http_methods(["GET", "POST"])
def reminders_view(request):
    if not reminders.enabled():
        return JsonResponse({"error": "Reminders aren't set up here."}, status=404)
    if request.method == "GET":
        return _reminder_list()
    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        return JsonResponse({"error": "Bad request"}, status=400)
    if not isinstance(body, dict):
        return JsonResponse({"error": "Bad request"}, status=400)
    action = body.get("action")
    if action == "add":
        departure = schedule.is_departure(body.get("departure"))
        try:
            lead = int(body.get("lead_min"))
        except (TypeError, ValueError):
            lead = None
        if departure is None or lead not in schedule.LEADS:
            return JsonResponse({"error": "That isn't a shuttle time."}, status=400)
        try:
            reminders.add(departure, lead, body.get("speakers"))
        except ValueError as e:
            return JsonResponse({"error": str(e)}, status=400)
        worker.poke()
    elif action == "cancel" and body.get("id"):
        if not reminders.cancel(str(body["id"])):
            return JsonResponse({"error": "That reminder is gone."}, status=404)
        worker.poke()
    else:
        return JsonResponse({"error": "Bad request"}, status=400)
    return _reminder_list()