import os
import time
import json
import random
import threading
import urllib.request
from queue import Queue, Empty
from dotenv import load_dotenv
from openai import AzureOpenAI
from concurrent.futures import ThreadPoolExecutor
from azure.eventhub import EventHubConsumerClient
from cloud.iot_hub_client import send_to_iot_hub

# ---------------------------------------------------------------------------
# 1. Setup
# ---------------------------------------------------------------------------
load_dotenv()

ENDPOINT        = "https://aeg45-mljsjydr-eastus2.cognitiveservices.azure.com/"
API_KEY         = os.getenv("AZURE_OPENAI_KEY")
API_VERSION     = "2024-12-01-preview"
DEPLOYMENT_NAME = "ecoguardian-gpt-4o"

NTFY_TOPIC        = os.getenv("NTFY_TOPIC")
EVENTHUB_CONN_STR = os.getenv("IOTHUB_EVENT_HUB_ENDPOINT")
EVENTHUB_NAME     = os.getenv("IOTHUB_EVENT_HUB_NAME")

client = AzureOpenAI(
    api_version=API_VERSION,
    azure_endpoint=ENDPOINT,
    api_key=API_KEY,
)

ROOMS = [
    {"dtId": "ecoguardian-dt-lab-a-205", "roomId": "Lab-A-205"},
    {"dtId": "ecoguardian-dt-lab-b-301", "roomId": "Lab-B-301"},
    {"dtId": "ecoguardian-dt-lab-c-412", "roomId": "Lab-C-412"}
]

ROOM_DISPLAY = {
    "Lab-A-205": "LAB A  (205)",
    "Lab-B-301": "LAB B  (301)",
    "Lab-C-412": "LAB C  (412)",
}

# How many iterations between terminal prints per mode
PRINT_EVERY = {"a": 3, "b": 1, "c": 5}

# ---------------------------------------------------------------------------
# 2. ANSI color codes
# ---------------------------------------------------------------------------
RESET = "\033[0m"
BOLD  = "\033[1m"

LAB_COLOR = {
    "Lab-A-205": "\033[96m",    # cyan
    "Lab-B-301": "\033[92m",    # bright green
    "Lab-C-412": "\033[35m",    # magenta
}

RISK_COLOR = {
    "Low":      "\033[32m",
    "Medium":   "\033[33m",
    "High":     "\033[91m",
    "Critical": "\033[95m",
    "Unknown":  "\033[90m",
}

# ---------------------------------------------------------------------------
# 3. IAQ thresholds
# ---------------------------------------------------------------------------
THRESHOLDS = {
    "co2":      {"medium": 800,  "high": 1500, "critical": 2500},
    "temp":     {"low_floor": 18.0, "medium": 24.4, "high": 29.0, "critical": 33.0},
    "humidity": {"low_floor": 20.0, "medium": 60.0, "high": 70.0, "critical": 80.0},
    "pm25":     {"medium": 12.0, "high": 35.0, "critical": 55.0},
}

AI_SYSTEM_PROMPT = (
    "You are EcoGuardian, an industrial hygienist AI monitoring university lab air quality.\n"
    "Classify risk using THESE EXACT thresholds — follow them precisely, no exceptions:\n\n"
    "CO2 (ppm):\n"
    "  LOW      < 800\n"
    "  MEDIUM   800 - 1499\n"
    "  HIGH    1500 - 2499\n"
    "  CRITICAL >= 2500\n\n"
    "Temperature (C):\n"
    "  LOW      < 18  (too cold, below OSHA floor)\n"
    "  MEDIUM  18 - 24.3  (OSHA comfort band)\n"
    "  HIGH    24.4 - 28.9  (above OSHA comfort ceiling)\n"
    "  CRITICAL >= 29  (OSHA/NIOSH heat stress risk zone)\n\n"
    "Humidity (%):\n"
    "  LOW      < 20  (too dry)\n"
    "  MEDIUM  20 - 59  (OSHA recommended)\n"
    "  HIGH    60 - 69  (mold risk)\n"
    "  CRITICAL >= 70  (mold and respiratory hazard)\n\n"
    "PM2.5 (ug/m3):\n"
    "  LOW      < 12  (EPA Good AQI)\n"
    "  MEDIUM  12 - 34\n"
    "  HIGH    35 - 54  (Unhealthy for Sensitive Groups)\n"
    "  CRITICAL >= 55  (Unhealthy — general population)\n\n"
    "Overall riskLevel = the HIGHEST level triggered by ANY single metric.\n"
    "If temperature >= 29C, riskLevel MUST be High or Critical.\n\n"
    "Return ONLY valid JSON:\n"
    '{"riskLevel": "Low|Medium|High|Critical", '
    '"causes": "1-2 sentences on which metrics are elevated and why", '
    '"solutions": "1-2 sentences of immediate technical fixes", '
    '"recommendations": "1-2 sentences of human safety actions"}'
)

# ---------------------------------------------------------------------------
# 4. Safe ranges used by Mode A for locked metrics
# ---------------------------------------------------------------------------
# Mode A: CO2, humidity, PM2.5 stay locked in LOW safe ranges.
# Only temperature uses the real ESP32 reading.
MODE_A_SAFE = {
    "co2":      (420,  780),    # always Low (<800)
    "humidity": (30,    58),    # always Low-Medium safe (20-59)
    "pm25":     (2.0,  11.0),   # always Low (<12)
}

# ---------------------------------------------------------------------------
# 5. Mode B weighted distribution
# ---------------------------------------------------------------------------
# Weighted buckets so all four risk levels appear in stress test.
# Weights: Low 25%, Medium 35%, High 25%, Critical 15%
MODE_B_BUCKETS = {
    "co2": [
        (0.25, (420,  799)),    # Low
        (0.35, (800, 1499)),    # Medium
        (0.25, (1500, 2499)),   # High
        (0.15, (2500, 3000)),   # Critical
    ],
    "temp": [
        (0.25, (18.0, 24.3)),
        (0.35, (24.4, 28.9)),
        (0.25, (29.0, 32.9)),
        (0.15, (33.0, 35.0)),
    ],
    "humidity": [
        (0.25, (20.0, 59.9)),
        (0.35, (15.0, 19.9)),   # too dry = Medium
        (0.25, (60.0, 69.9)),
        (0.15, (70.0, 85.0)),
    ],
    "pm25": [
        (0.25, (1.0,  11.9)),
        (0.35, (12.0, 34.9)),
        (0.25, (35.0, 54.9)),
        (0.15, (55.0, 80.0)),
    ],
}

def _weighted_sample(buckets):
    """Pick a random value from a weighted list of (weight, (lo, hi)) tuples."""
    r = random.random()
    cumulative = 0.0
    for weight, (lo, hi) in buckets:
        cumulative += weight
        if r <= cumulative:
            if isinstance(lo, int) and isinstance(hi, int):
                return random.randint(lo, hi)
            return round(random.uniform(lo, hi), 1)
    lo, hi = buckets[-1][1]
    return round(random.uniform(lo, hi), 1)


# ---------------------------------------------------------------------------
# 6. Realistic params for Mode C drift
# ---------------------------------------------------------------------------
REALISTIC_PARAMS = {
    "co2":      (400,   3000,  20.0,  100.0, 0.05),
    "temp":     (15.0,  35.0,   0.15,   1.5, 0.04),
    "humidity": (15.0,  85.0,   0.5,    4.0, 0.03),
    "pm25":     (1.0,   80.0,   1.5,   20.0, 0.06),
}

MODE_C_START = {
    "co2":      (1600, 2800),
    "temp":     (29.0, 34.0),
    "humidity": (15.0, 19.0),
    "pm25":     (40.0, 75.0),
}

SAFE_TARGETS = {
    "co2":      700.0,
    "temp":      21.0,
    "humidity":  45.0,
    "pm25":       8.0,
}

REMEDIATION_PULL    = 0.13
PROMPT_COOLDOWN     = 10
ALERT_COOLDOWN_SECS = 300

METRIC_LABELS = {
    "co2":      "CO2 levels",
    "temp":     "Temperature",
    "humidity": "Humidity",
    "pm25":     "PM2.5 particulates",
}

_alerted_rooms      = {}
_alerted_rooms_lock = threading.Lock()
_print_counter      = {room["roomId"]: 0 for room in ROOMS}


# ---------------------------------------------------------------------------
# 7. Terminal print helper
# ---------------------------------------------------------------------------
def print_lab_block(room_id, risk_level, co2, temp, humidity, pm25,
                    temp_source, cause, solution,
                    remediating, action_taken, cooldown, mode):

    lab_color  = LAB_COLOR.get(room_id, RESET)
    risk_color = RISK_COLOR.get(risk_level, RESET)
    width      = 58
    border     = "─" * width

    status_parts = []
    if remediating:
        status_parts.append("🔧 REMEDIATING")
    if mode == "c" and action_taken != "NULL":
        status_parts.append(f"AI Action: {action_taken}")
    if cooldown > 0:
        status_parts.append(f"Cooldown: {cooldown}")
    status_str = "  |  ".join(status_parts)

    temp_tag = " [REAL]" if temp_source == "real" else ""

    def wrap(text, indent=12):
        words = str(text).split()
        lines, line = [], ""
        for word in words:
            if len(line) + len(word) + 1 <= width - indent:
                line = (line + " " + word).strip()
            else:
                if line:
                    lines.append(line)
                line = word
        if line:
            lines.append(line)
        pad = " " * indent
        return ("\n" + pad).join(lines)

    display = ROOM_DISPLAY.get(room_id, room_id)
    ts      = time.strftime('%H:%M:%S')

    print(f"\n{lab_color}{border}{RESET}")
    print(f"{lab_color}{BOLD}  {display}   {ts}{RESET}")
    print(f"{lab_color}{border}{RESET}")
    print(f"  Warning Level:  {risk_color}{BOLD}{risk_level.upper()}{RESET}")
    if status_str:
        print(f"  Status:         {status_str}")
    print()
    print(f"  Metrics:")
    print(f"    CO2:       {co2} ppm")
    print(f"    Temp:      {temp}°C{temp_tag}")
    print(f"    Humidity:  {humidity}%")
    print(f"    PM2.5:     {pm25} µg/m³")

    if risk_level not in ("Low", "Unknown"):
        print()
        print(f"  Cause:")
        print(f"            {wrap(cause)}")
        print()
        print(f"  Solution:")
        print(f"            {wrap(solution)}")

    print(f"{lab_color}{border}{RESET}")


# ---------------------------------------------------------------------------
# 8. Local risk classifier
# ---------------------------------------------------------------------------
def _local_risk(co2, temp, humidity, pm25):
    def co2_risk(v):
        if v >= THRESHOLDS["co2"]["critical"]: return 3
        if v >= THRESHOLDS["co2"]["high"]:     return 2
        if v >= THRESHOLDS["co2"]["medium"]:   return 1
        return 0

    def temp_risk(v):
        if v >= THRESHOLDS["temp"]["critical"]:  return 3
        if v >= THRESHOLDS["temp"]["high"]:      return 2
        if v >= THRESHOLDS["temp"]["medium"]:    return 1
        if v <  THRESHOLDS["temp"]["low_floor"]: return 1
        return 0

    def hum_risk(v):
        if v >= THRESHOLDS["humidity"]["critical"]:  return 3
        if v >= THRESHOLDS["humidity"]["high"]:      return 2
        if v >= THRESHOLDS["humidity"]["medium"]:    return 1
        if v <  THRESHOLDS["humidity"]["low_floor"]: return 1
        return 0

    def pm_risk(v):
        if v >= THRESHOLDS["pm25"]["critical"]: return 3
        if v >= THRESHOLDS["pm25"]["high"]:     return 2
        if v >= THRESHOLDS["pm25"]["medium"]:   return 1
        return 0

    level = max(co2_risk(co2), temp_risk(temp), hum_risk(humidity), pm_risk(pm25))
    return ["Low", "Medium", "High", "Critical"][level]


# ---------------------------------------------------------------------------
# 9. ESP32 temperature state
# ---------------------------------------------------------------------------
esp32_state = {
    "temperature":  None,
    "last_updated": 0,
    "stale_after":  30,
}
esp32_lock        = threading.Lock()
esp32_ready_event = threading.Event()


def get_esp32_temp():
    with esp32_lock:
        age = time.time() - esp32_state["last_updated"]
        if esp32_state["temperature"] is not None and age < esp32_state["stale_after"]:
            return esp32_state["temperature"], True
    return None, False


def esp32_listener():
    if not EVENTHUB_CONN_STR or not EVENTHUB_NAME:
        esp32_ready_event.set()
        return

    def on_event(partition_context, event):
        try:
            device_id = event.system_properties.get(b"iothub-connection-device-id", b"")
            if isinstance(device_id, bytes):
                device_id = device_id.decode("utf-8")
            if device_id != "esp32-temp":
                return
            data = json.loads(event.body_as_str())
            temp = data.get("temperature")
            if temp is not None and isinstance(temp, (int, float)):
                with esp32_lock:
                    esp32_state["temperature"]  = float(round(temp, 1))
                    esp32_state["last_updated"] = time.time()
                if not esp32_ready_event.is_set():
                    esp32_ready_event.set()
            partition_context.update_checkpoint()
        except Exception:
            pass

    def on_error(partition_context, error):
        pass

    while True:
        try:
            consumer = EventHubConsumerClient.from_connection_string(
                conn_str=EVENTHUB_CONN_STR,
                consumer_group="$Default",
                eventhub_name=EVENTHUB_NAME,
            )
            with consumer:
                consumer.receive(on_event=on_event, on_error=on_error, starting_position="-1")
        except Exception:
            time.sleep(5)


# ---------------------------------------------------------------------------
# 10. Push notification (silent)
# ---------------------------------------------------------------------------
def send_push_alert(room_id, risk_level, possible_causes, possible_solutions):
    if not NTFY_TOPIC:
        return
    now = time.time()
    with _alerted_rooms_lock:
        last = _alerted_rooms.get(room_id, 0)
        if now - last < ALERT_COOLDOWN_SECS:
            return
        _alerted_rooms[room_id] = now

    def clean(text):
        return str(text).encode("ascii", "ignore").decode("ascii")

    body = (
        f"EcoGuardian ALERT\n"
        f"Room: {room_id}\n"
        f"Risk: {risk_level}\n"
        f"Problem: {clean(possible_causes)}\n"
        f"Solution: {clean(possible_solutions)}"
    ).encode("utf-8")

    try:
        req = urllib.request.Request(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=body,
            headers={
                "Title":    f"EcoGuardian ALERT - {room_id} {risk_level}".encode("utf-8"),
                "Priority": "urgent" if risk_level == "Critical" else "high",
                "Tags":     "warning,lab",
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10):
            pass
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 11. Per-room state factory
# ---------------------------------------------------------------------------
def _make_state(mode, room_id):
    if mode == "c":
        co2      = float(random.randint(*MODE_C_START["co2"]))
        temp     = round(random.uniform(*MODE_C_START["temp"]), 1)
        humidity = round(random.uniform(*MODE_C_START["humidity"]), 1)
        pm25     = round(random.uniform(*MODE_C_START["pm25"]), 1)
    else:
        co2, temp, humidity, pm25 = 650.0, 21.0, 45.0, 7.0

    return {
        "co2": co2, "temp": temp, "humidity": humidity, "pm25": pm25,
        "co2_drift":  1, "temp_drift":  1, "hum_drift": -1, "pm_drift":  1,
        "co2_ttl":   18, "temp_ttl":   25, "hum_ttl":   20, "pm_ttl":   15,
        "remediating":        False,
        "remediation_action": None,
        "ai_action_taken":    "NULL",
        "prompt_pending":     False,
        "cooldown_remaining": 0,
    }

ROOM_STATE = {}


# ---------------------------------------------------------------------------
# 12. Thread-safe input queue (Mode C)
# ---------------------------------------------------------------------------
prompt_queue   = Queue()
response_store = {}
response_lock  = threading.Lock()


def input_thread_worker():
    width = 58
    while True:
        try:
            room_id, metric_label, risk_level, ai_action = prompt_queue.get(timeout=1)
        except Empty:
            continue

        lab_color = LAB_COLOR.get(room_id, RESET)

        print(f"\n{lab_color}{'━'*width}{RESET}")
        print(f"{lab_color}{BOLD}  ⚠️  {risk_level.upper()} RISK — {ROOM_DISPLAY.get(room_id, room_id)}{RESET}")
        print(f"{lab_color}{'━'*width}{RESET}")
        print(f"  Metric at risk:      {metric_label}")
        print(f"  Recommended action:  {ai_action}")
        print(f"{lab_color}{'━'*width}{RESET}")

        while True:
            answer = input("  Accept AI recommendation? (Y / N): ").strip().lower()
            if answer in ("y", "n"):
                break
            print("  Please enter Y or N.")

        with response_lock:
            response_store[room_id] = answer

        prompt_queue.task_done()


# ---------------------------------------------------------------------------
# 13. Helpers
# ---------------------------------------------------------------------------
def _worst_metric(state):
    scores = {
        "co2":      (state["co2"]      - SAFE_TARGETS["co2"])  / SAFE_TARGETS["co2"],
        "temp":     (state["temp"]     - SAFE_TARGETS["temp"]) / SAFE_TARGETS["temp"],
        "humidity": abs(state["humidity"] - SAFE_TARGETS["humidity"]) / SAFE_TARGETS["humidity"],
        "pm25":     (state["pm25"]     - SAFE_TARGETS["pm25"]) / SAFE_TARGETS["pm25"],
    }
    return max(scores, key=scores.get)


def _choose_action(state):
    if state["co2"] >= THRESHOLDS["co2"]["high"]:
        return "Increase ventilation and flush HVAC system"
    if state["pm25"] >= THRESHOLDS["pm25"]["high"]:
        return "Activate HEPA filtration units immediately"
    if state["temp"] >= THRESHOLDS["temp"]["high"]:
        return "Engage cooling system and increase air circulation"
    if state["humidity"] >= THRESHOLDS["humidity"]["high"]:
        return "Activate dehumidification system"
    if state["humidity"] < THRESHOLDS["humidity"]["low_floor"]:
        return "Activate humidification system"
    return "Multi-parameter HVAC environmental control"


def _all_safe(state):
    return (
        state["co2"]      <  THRESHOLDS["co2"]["medium"] and
        THRESHOLDS["temp"]["low_floor"] <= state["temp"] < THRESHOLDS["temp"]["medium"] and
        THRESHOLDS["humidity"]["low_floor"] <= state["humidity"] < THRESHOLDS["humidity"]["medium"] and
        state["pm25"]     <  THRESHOLDS["pm25"]["medium"]
    )


# ---------------------------------------------------------------------------
# 14. Mode C metric step functions
# ---------------------------------------------------------------------------
def _step_normal(state, key, drift_key, ttl_key):
    val   = state[key]
    drift = state[drift_key]
    ttl   = state[ttl_key]
    lo, hi, normal_step, spike_step, spike_prob = REALISTIC_PARAMS[key]
    step = (random.uniform(spike_step * 0.5, spike_step)
            if random.random() < spike_prob
            else random.uniform(0, normal_step)) * drift
    val  = round(val + step + random.uniform(-normal_step * 0.3, normal_step * 0.3), 1)
    val  = max(lo, min(hi, val))
    ttl -= 1
    if ttl <= 0:
        drift = -drift
        ttl   = random.randint(10, 35)
    state[key]       = val
    state[drift_key] = drift
    state[ttl_key]   = ttl
    return val


def _step_remediate(state, key):
    val    = state[key]
    target = SAFE_TARGETS[key]
    gap    = target - val
    pull   = gap * REMEDIATION_PULL
    lo, hi, normal_step, *_ = REALISTIC_PARAMS[key]
    noise  = random.gauss(0, normal_step * 0.25)
    val    = round(val + pull + noise, 1)
    val    = max(lo, min(hi, val))
    state[key] = val
    return val


def _step_drift_bad(state, key, drift_key, ttl_key):
    """
    Forced worsening drift when user declines AI action.
    Locked upward — never reverses — ensuring conditions
    never improve after a NO answer.
    """
    val = state[key]
    lo, hi, normal_step, spike_step, _ = REALISTIC_PARAMS[key]

    # Use a larger step than normal to make the decline visible
    step = random.uniform(normal_step * 1.0, spike_step * 0.7)

    # Humidity should DROP (too dry = bad), everything else rises
    if key == "humidity":
        step = -abs(step)
    else:
        step = abs(step)

    val = round(val + step, 1)
    val = max(lo, min(hi, val))

    # Lock drift in the worsening direction permanently
    state[key]       = val
    state[drift_key] = -1 if key == "humidity" else 1
    state[ttl_key]   = 9999   # never auto-reverse
    return val


# ---------------------------------------------------------------------------
# 15. Mode A — locked safe metrics, real temp only
# ---------------------------------------------------------------------------
def next_mode_a(room_id):
    """
    CO2, humidity, PM2.5 stay within LOW safe ranges (no drift).
    Temperature is handled externally via ESP32 — this just returns
    a safe fallback in case the sensor is offline.
    """
    co2      = random.randint(*MODE_A_SAFE["co2"])
    humidity = random.randint(*MODE_A_SAFE["humidity"])
    pm25     = round(random.uniform(*MODE_A_SAFE["pm25"]), 1)
    # Temperature fallback — overridden by real ESP32 reading in simulate_room
    temp     = round(random.uniform(19.0, 23.0), 1)
    return co2, temp, humidity, pm25


# ---------------------------------------------------------------------------
# 16. Mode B — weighted pseudorandom across all risk levels
# ---------------------------------------------------------------------------
def next_mode_b():
    co2      = int(_weighted_sample(MODE_B_BUCKETS["co2"]))
    temp     = round(_weighted_sample(MODE_B_BUCKETS["temp"]), 1)
    humidity = int(_weighted_sample(MODE_B_BUCKETS["humidity"]))
    pm25     = round(_weighted_sample(MODE_B_BUCKETS["pm25"]), 1)
    return co2, temp, humidity, pm25


# ---------------------------------------------------------------------------
# 17. Mode C — starts High/Critical, interactive remediation
# ---------------------------------------------------------------------------
def next_mode_c(room_id, print_lock):
    state = ROOM_STATE[room_id]

    # Check if input thread posted an answer
    if state["prompt_pending"]:
        with response_lock:
            answer = response_store.pop(room_id, None)
        if answer is not None:
            state["prompt_pending"]     = False
            state["cooldown_remaining"] = PROMPT_COOLDOWN
            if answer == "y":
                state["remediating"]     = True
                state["ai_action_taken"] = "Yes"
                with print_lock:
                    lc = LAB_COLOR.get(room_id, RESET)
                    print(f"\n  {lc}✅ {ROOM_DISPLAY.get(room_id, room_id)} — remediation started.{RESET}")
            else:
                # NO — lock all drifts in worsening direction permanently
                state["ai_action_taken"] = "No"
                state["co2_drift"]  =  1;  state["co2_ttl"]  = 9999
                state["temp_drift"] =  1;  state["temp_ttl"] = 9999
                state["hum_drift"]  = -1;  state["hum_ttl"]  = 9999
                state["pm_drift"]   =  1;  state["pm_ttl"]   = 9999
                with print_lock:
                    lc = LAB_COLOR.get(room_id, RESET)
                    print(f"\n  {lc}❌ {ROOM_DISPLAY.get(room_id, room_id)} — action declined. "
                          f"Conditions will worsen and will NOT recover.{RESET}")
        else:
            # Still waiting — keep drifting (or worsening if already declined)
            if state["ai_action_taken"] == "No":
                co2      = int(_step_drift_bad(state, "co2",      "co2_drift",  "co2_ttl"))
                temp     = round(_step_drift_bad(state, "temp",     "temp_drift", "temp_ttl"), 1)
                humidity = int(_step_drift_bad(state, "humidity",  "hum_drift",  "hum_ttl"))
                pm25     = round(_step_drift_bad(state, "pm25",     "pm_drift",   "pm_ttl"), 1)
            else:
                co2      = int(_step_normal(state, "co2",      "co2_drift",  "co2_ttl"))
                temp     = round(_step_normal(state, "temp",     "temp_drift", "temp_ttl"), 1)
                humidity = int(_step_normal(state, "humidity",  "hum_drift",  "hum_ttl"))
                pm25     = round(_step_normal(state, "pm25",     "pm_drift",   "pm_ttl"), 1)
            return co2, temp, humidity, pm25

    if state["cooldown_remaining"] > 0:
        state["cooldown_remaining"] -= 1

    if state["remediating"]:
        co2      = _step_remediate(state, "co2")
        temp     = _step_remediate(state, "temp")
        humidity = _step_remediate(state, "humidity")
        pm25     = _step_remediate(state, "pm25")
        if _all_safe(state):
            with print_lock:
                lc = LAB_COLOR.get(room_id, RESET)
                print(f"\n  {lc}✅ REMEDIATION COMPLETE — {ROOM_DISPLAY.get(room_id, room_id)}{RESET}")
            state["remediating"]        = False
            state["remediation_action"] = None
            state["ai_action_taken"]    = "NULL"
            state["cooldown_remaining"] = PROMPT_COOLDOWN
            # Drift back up so the demo cycles again
            state["co2_drift"]  =  1;  state["co2_ttl"]  = random.randint(15, 25)
            state["temp_drift"] =  1;  state["temp_ttl"] = random.randint(15, 25)
            state["hum_drift"]  = -1;  state["hum_ttl"]  = random.randint(15, 25)
            state["pm_drift"]   =  1;  state["pm_ttl"]   = random.randint(15, 25)

    else:
        # Worsen if declined, normal drift otherwise
        if state["ai_action_taken"] == "No":
            co2      = int(_step_drift_bad(state, "co2",      "co2_drift",  "co2_ttl"))
            temp     = round(_step_drift_bad(state, "temp",     "temp_drift", "temp_ttl"), 1)
            humidity = int(_step_drift_bad(state, "humidity",  "hum_drift",  "hum_ttl"))
            pm25     = round(_step_drift_bad(state, "pm25",     "pm_drift",   "pm_ttl"), 1)
        else:
            co2      = int(_step_normal(state, "co2",      "co2_drift",  "co2_ttl"))
            temp     = round(_step_normal(state, "temp",     "temp_drift", "temp_ttl"), 1)
            humidity = int(_step_normal(state, "humidity",  "hum_drift",  "hum_ttl"))
            pm25     = round(_step_normal(state, "pm25",     "pm_drift",   "pm_ttl"), 1)

        risk = _local_risk(co2, temp, humidity, pm25)
        if (risk in ("High", "Critical")
                and not state["prompt_pending"]
                and state["cooldown_remaining"] == 0
                and state["ai_action_taken"] != "No"):   # don't re-prompt after a NO
            action = _choose_action(state)
            worst  = METRIC_LABELS[_worst_metric(state)]
            state["remediation_action"] = action
            state["prompt_pending"]     = True
            prompt_queue.put((room_id, worst, risk, action))

    return (
        int(state["co2"]),
        round(state["temp"], 1),
        int(state["humidity"]),
        round(state["pm25"], 1),
    )


# ---------------------------------------------------------------------------
# 18. AI analysis
# ---------------------------------------------------------------------------
def get_thorough_ai_analysis(room_id, co2, temp, humidity, pm25):
    user_msg = (
        f"Room {room_id}: CO2={co2}ppm, Temp={temp}C, "
        f"Humidity={humidity}%, PM2.5={pm25}ug/m3."
    )
    try:
        response = client.chat.completions.create(
            model=DEPLOYMENT_NAME,
            messages=[
                {"role": "system", "content": AI_SYSTEM_PROMPT},
                {"role": "user",   "content": user_msg}
            ],
            temperature=0.2,
            response_format={"type": "json_object"}
        )
        return json.loads(response.choices[0].message.content)
    except Exception:
        return {
            "riskLevel":       "Unknown",
            "causes":          "API error — check connection",
            "solutions":       "Verify Azure OpenAI credentials",
            "recommendations": "N/A"
        }


# ---------------------------------------------------------------------------
# 19. Room simulation thread
# ---------------------------------------------------------------------------
def simulate_room(room_info, print_lock, mode):
    dt_id   = room_info["dtId"]
    room_id = room_info["roomId"]
    every   = PRINT_EVERY[mode]

    # Lab-A-205: wait for first ESP32 reading before starting
    if room_id == "Lab-A-205" and EVENTHUB_CONN_STR and EVENTHUB_NAME:
        with print_lock:
            lc = LAB_COLOR.get(room_id, RESET)
            print(f"  {lc}⏳ Waiting for ESP32 sensor — Lab-A-205...{RESET}")
        got = esp32_ready_event.wait(timeout=30)
        if got:
            real_temp, is_real = get_esp32_temp()
            if is_real:
                ROOM_STATE[room_id]["temp"] = real_temp
                with print_lock:
                    lc = LAB_COLOR.get(room_id, RESET)
                    print(f"  {lc}🌡️  Lab-A-205 sensor online — {real_temp}°C{RESET}")
        else:
            with print_lock:
                print(f"  ⚠️  ESP32 timeout — Lab-A-205 using simulated temperature")

    while True:
        if mode == "a":
            co2, temp, humidity, pm25 = next_mode_a(room_id)
        elif mode == "c":
            co2, temp, humidity, pm25 = next_mode_c(room_id, print_lock)
        else:
            co2, temp, humidity, pm25 = next_mode_b()

        # Lab-A-205: always override temp with real ESP32 reading
        temp_source = "simulated"
        if room_id == "Lab-A-205":
            real_temp, is_real = get_esp32_temp()
            if is_real:
                temp        = real_temp
                temp_source = "real"
                ROOM_STATE[room_id]["temp"] = real_temp

        ai_data = get_thorough_ai_analysis(room_id, co2, temp, humidity, pm25)

        state        = ROOM_STATE.get(room_id, {})
        remediating  = state.get("remediating",     False)
        action_taken = state.get("ai_action_taken", "NULL") if mode != "b" else "NULL"
        cooldown     = state.get("cooldown_remaining", 0)

        payload = {
            "dtId":              dt_id,
            "roomId":            room_id,
            "co2":               co2,
            "temperature":       temp,
            "humidity":          humidity,
            "pm2_5":             pm25,
            "riskLevel":         ai_data.get("riskLevel"),
            "possibleCauses":    ai_data.get("causes"),
            "possibleSolutions": ai_data.get("solutions"),
            "aiRecommendations": ai_data.get("recommendations"),
            "remediating":       remediating,
            "aiActionTaken":     action_taken,
            "tempSource":        temp_source,
        }

        _print_counter[room_id] += 1
        # Always print on High/Critical, otherwise respect frequency
        should_print = (
            (_print_counter[room_id] % every == 0) or
            payload["riskLevel"] in ("High", "Critical")
        )

        with print_lock:
            if should_print:
                print_lab_block(
                    room_id      = room_id,
                    risk_level   = ai_data.get("riskLevel", "Unknown"),
                    co2          = co2,
                    temp         = temp,
                    humidity     = humidity,
                    pm25         = pm25,
                    temp_source  = temp_source,
                    cause        = ai_data.get("causes", ""),
                    solution     = ai_data.get("solutions", ""),
                    remediating  = remediating,
                    action_taken = action_taken,
                    cooldown     = cooldown,
                    mode         = mode,
                )
            if payload["riskLevel"] in ("High", "Critical"):
                send_push_alert(
                    room_id,
                    payload["riskLevel"],
                    payload["possibleCauses"],
                    payload["possibleSolutions"]
                )

        send_to_iot_hub(payload)
        time.sleep(6)


# ---------------------------------------------------------------------------
# 20. Entry point
# ---------------------------------------------------------------------------
def prompt_mode():
    width = 58
    print(f"\n{'═'*width}")
    print(f"{'EcoGuardianAI  —  Select Simulation Mode':^{width}}")
    print(f"{'═'*width}")
    print(f"  A  —  Physical Sensor Demo")
    print(f"         CO2, humidity, PM2.5 locked in safe ranges")
    print(f"         Only temperature reacts (real ESP32 sensor)")
    print(f"         Prints every 3 iterations")
    print()
    print(f"  B  —  Stress Test")
    print(f"         Weighted pseudorandom across all risk levels")
    print(f"         Low 25% / Medium 35% / High 25% / Critical 15%")
    print(f"         Prints every iteration")
    print()
    print(f"  C  —  AI Action Demo")
    print(f"         Starts High/Critical — accept or decline AI")
    print(f"         Decline = permanent worsening, no recovery")
    print(f"         Accept = gradual remediation back to safe")
    print(f"         Prints every 5 iterations")
    print(f"{'═'*width}")
    while True:
        choice = input("\n  Enter mode (A, B, or C): ").strip().lower()
        if choice in ("a", "b", "c"):
            return choice
        print("  Please type A, B, or C.")


def run_simulation(mode):
    labels = {"a": "Physical Sensor Demo", "b": "Stress Test", "c": "AI Action Demo"}
    width  = 58
    print(f"\n{'═'*width}")
    print(f"  EcoGuardianAI  —  {labels[mode]}")
    print(f"  Rooms: {', '.join(r['roomId'] for r in ROOMS)}")
    print(f"{'═'*width}\n")

    for room in ROOMS:
        ROOM_STATE[room["roomId"]] = _make_state(mode, room["roomId"])

    print_lock = threading.Lock()

    esp_thread = threading.Thread(target=esp32_listener, daemon=True)
    esp_thread.start()

    if mode == "c":
        t = threading.Thread(target=input_thread_worker, daemon=True)
        t.start()

    try:
        with ThreadPoolExecutor(max_workers=len(ROOMS)) as executor:
            futures = [
                executor.submit(simulate_room, room, print_lock, mode)
                for room in ROOMS
            ]
            for future in futures:
                future.result()
    except KeyboardInterrupt:
        print("\n\n  Simulation stopped.\n")


if __name__ == "__main__":
    if not API_KEY:
        print("❌  AZURE_OPENAI_KEY is missing from .env")
    else:
        selected_mode = prompt_mode()
        run_simulation(selected_mode)
