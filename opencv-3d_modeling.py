# ═══════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════
MODEL_FOLDER       = "/Users/harinathrayala/Desktop/plymodel"
VOSK_MODEL_PATH    = "/Users/harinathrayala/vosk-model/vosk-model-small-en-us-0.15"
MOVE_SENS          = 12
SMOOTH             = 0.25
PINCH_SCALE_SPEED  = 0.05
COMMAND_COOLDOWN   = 0.4
AUTOSAVE_INTERVAL  = 300
SCREENSHOT_FOLDER  = "/Users/harinathrayala/Desktop/blender_shots"
# ── Standard library ────────────────────────────────────────────────────────────
import math
import os
import threading
import time
import json
import random
from queue import Queue, Empty
from collections import deque

# ── Blender Python API ──────────────────────────────────────────────────────────
import bpy
import bmesh

# ── Third-party (installed into Blender's Python) ───────────────────────────────
import numpy as np

try:
    import cv2
    cv2.setNumThreads(1)          # prevents macOS GCD thread conflicts
    CV2_OK = True
except ImportError:
    CV2_OK = False
    print("[WARN] opencv-python not found — camera feed disabled")

try:
    import mediapipe as mp
    MP_OK = True
except ImportError:
    MP_OK = False
    print("[WARN] mediapipe not found — gesture control disabled")

# OpenAI completely removed — 100% local
OPENAI_OK      = False
JARVIS_ENABLED = False

try:
    #import vosk
    import sounddevice as sd
    import json as _json
    VOSK_OK = False  # not used — Google SR is more accurate
    print("[OK] Vosk local speech recognition ready")
except ImportError:
    VOSK_OK = False
    print("[WARN] vosk not found — install: pip install vosk sounddevice")

try:
    import pyttsx3
    TTS_OK = True
except ImportError:
    TTS_OK = False
    print("[WARN] pyttsx3 not found — TTS disabled")



# ═══════════════════════════════════════════════════════════════════════════════
#  GLOBALS
# ═══════════════════════════════════════════════════════════════════════════════
command_queue         = Queue()
running               = True          # set False to stop all threads + timer
prev_two_hand_dist    = None
last_command_time     = 0.0
analysis_cache        = {}
jarvis_memory         = []
_last_autosave = time.time()

_waveform_samples = deque(maxlen=48)
_waveform_lock    = threading.Lock()
try:
    from collections import deque
except ImportError:
    pass
# ── Help Menu State ──────────────────────────────────────────────
_menu_visible    = False
_menu_page       = 0
_menu_anim_start = 0.0   # time when menu was opened (for entry animation)
# ── Phase 1 globals ──────────────────────────────────────────────
_fps_times        = deque(maxlen=60)   # stores last 60 frame timestamps
_command_log      = deque(maxlen=5)    # last 5 voice commands
_mood_color       = (0, 255, 180)      # current HUD accent color
_mood_target      = (0, 255, 180)      # color we are lerping toward
_status_messages  = [
    "NEURAL SYNC STABLE",
    "QUANTUM MESH VERIFIED",
    "HOLOGRAPHIC LINK ACTIVE",
    "PARTICLE STREAM NOMINAL",
    "DIMENSIONAL RIFT SEALED",
    "BIOSCAN COMPLETE",
    "FLUX CAPACITOR CHARGED",
    "TACHYON PULSE DETECTED",
    "WARP CORE NOMINAL",
    "SUBSPACE FREQUENCY LOCKED",
    "DARK MATTER CONTAINED",
    "ANTIMATTER POD STABLE",
    "NEURAL NET CALIBRATED",
    "QUANTUM TUNNEL OPEN",
    "CHRONOSHIFT STANDBY",
]
_status_index     = 0
_status_timer     = 0.0
_status_interval  = 4.0   # seconds between status changes

# ── Phase 2 globals ──────────────────────────────────────────────
# Face / eye tracking
_face_mesh        = None
_eye_cursor_x     = 0.0
_eye_cursor_y     = 0.0
_eye_smooth_x     = 0.0
_eye_smooth_y     = 0.0
_face_tracking_on = False

# Viewport camera control
_viewport_rot_x   = 0.0
_viewport_rot_z   = 0.0
_last_hand_vp_x   = None
_last_hand_vp_y   = None
_viewport_mode    = False   # True = hand controls viewport, False = object

# Timeline
_timeline_playing = False

# Blender alert hook
_last_alert       = ""
_alert_time       = 0.0

# ── Phase 3 globals ──────────────────────────────────────────────
import anthropic

ANTHROPIC_API_KEY     = "your-api-key-here"   # get from console.anthropic.com
_ai_client            = None
_ai_busy              = False
_ai_response_queue    = Queue()

# Pose detection
_pose_detector        = None
_pose_tracking_on     = False
_left_hand_lm         = None
_right_hand_lm        = None
_dual_hand_mode       = False

# AR overlay
_ar_mode              = False
_ar_surface_y         = 0.0   # estimated ground plane Y in camera space
_ar_homography        = None

# Voice to mesh
_vtm_listening        = False
_vtm_last_shape       = ""
#---------------------------------------------------------------------
MENU_PAGES = [

    {
        "title": "◈ MOVEMENT COMMANDS",
        "color": (0, 255, 180),
        "items": [
            ("move left / right / up / down",  "Move object on X/Z axis"),
            ("move forward / back",            "Move object on Y axis"),
            ("scale up / make bigger",         "Increase object size ×1.5"),
            ("scale down / make smaller",      "Decrease object size ×0.5"),
            ("normalize",                      "Reset scale to standard"),
            ("snap",                           "Snap object to nearest grid"),
        ]
    },
    {
        "title": "◈ MIRROR & ARRAY",
        "color": (0, 220, 255),
        "items": [
            ("mirror x",   "Mirror object on X axis"),
            ("mirror y",   "Mirror object on Y axis"),
            ("mirror z",   "Mirror object on Z axis"),
            ("array 2",    "Duplicate object 2 times"),
            ("array 3",    "Duplicate object 3 times"),
            ("array 5",    "Duplicate object 5 times"),
        ]
    },
 
    {
        "title": "◈ RENDER & EXPORT",
        "color": (255, 180, 0),
        "items": [
            ("use cycles",   "Switch to Cycles renderer"),
            ("use eevee",    "Switch to EEVEE renderer"),
            ("export glb",   "Export as .glb file"),
            ("export fbx",   "Export as .fbx file"),
            ("export obj",   "Export as .obj file"),
            ("screenshot",   "Save render preview to Desktop"),
        ]
    },
    {
        "title": "◈ SCENE & HISTORY",
        "color": (255, 220, 0),
        "items": [
            ("undo",           "Undo last action"),
            ("redo",           "Redo last undone action"),
            ("scene",          "List all objects on HUD"),
            ("auto save",      "Save .blend file now"),
            ("select <name>",  "Select object by name"),
            ("delete",         "Delete active object"),
        ]
    },

    #-------------------------------------------------------------------------
   
    {
        "title": "◈ MESH OPERATIONS",
        "color": (0, 180, 255),
        "items": [
            ("analyze / analyse",   "Score mesh quality 0-100"),
            ("auto fix / fix model","Remove doubles, fix normals"),
            ("smooth",              "Shade smooth + subdivision"),
            ("decimate",            "Reduce polygons by 50%"),
            ("auto uv",             "Smart UV unwrap"),
            ("wireframe",           "Toggle wireframe overlay"),
        ]
    },
    {
        "title": "◈ MATERIALS & LIGHTING",
        "color": (180, 0, 255),
        "items": [
            ("red / green / blue / yellow",  "Apply base colour"),
            ("white / black",                "Apply base colour"),
            ("cinematic light",              "3-point warm lighting"),
            ("sci fi light",                 "Cyan/purple sci-fi rig"),
            ("studio light",                 "Clean white studio"),
            ("dramatic light",               "Hard single source"),
        ]
    },
   
    {
        "title": "◈ GESTURES (HAND)",
        "color": (0, 255, 120),
        "items": [
            ("Move palm",          "Object follows your hand"),
            ("Pinch (thumb+index)","Scale object up"),
            ("Two hands spread",   "Move object forward/back"),
            ("Tilt wrist",         "Rotate object"),
        ]
    },
    {
        "title": "◈ SYSTEM COMMANDS",
        "color": (255, 80, 80),
        "items": [
            ("import <name>",  "Import file from model folder"),
            ("select <name>",  "Select object by name"),
            ("delete",         "Delete active object"),
            ("menu",           "Toggle this help menu"),
            ("exit / quit",    "Shut down the system"),
        ]
    },
    {
        "title": "◈ PHASE 1 — HUD & DISPLAY",
        "color": (0, 255, 180),
        "items": [
            ("auto saved",              "File saves every 5 minutes"),
            ("wireframe",               "Toggle wireframe overlay"),
            ("screenshot",              "Save render to Desktop"),
            ("scene",                   "List all objects on HUD"),
            ("FPS counter",             "Always visible top-right"),
            ("mesh stats",              "Verts/faces/tris top-left"),
        ]
    },
    {
        "title": "◈ PHASE 2 — TIMELINE & FACE",
        "color": (0, 220, 255),
        "items": [
            ("play animation",          "Play timeline"),
            ("stop animation",          "Stop / pause timeline"),
            ("next frame",              "Advance one frame"),
            ("previous frame",          "Go back one frame"),
            ("go to start / end",       "Jump to first or last frame"),
            ("enable face tracking",    "Eye cursor control ON"),
            ("disable face tracking",   "Eye cursor control OFF"),
            ("viewport mode",           "Hand orbits 3D camera"),
            ("object mode",             "Hand moves object again"),
        ]
    },
    {
        "title": "◈ PHASE 2 — VIEWPORT CONTROL",
        "color": (0, 180, 255),
        "items": [
            ("viewport mode",           "Hand rotates viewport camera"),
            ("object mode",             "Hand moves object"),
            ("toggle viewport",         "Switch between both modes"),
            ("Move palm",               "Orbit camera in viewport mode"),
            ("Tilt wrist",              "Roll camera angle"),
            ("Two hands spread",        "Zoom viewport in/out"),
        ]
    },
    {
        "title": "◈ PHASE 3 — AI MATERIALS",
        "color": (180, 0, 255),
        "items": [
            ("make it futuristic",      "Cyan metallic sci-fi look"),
            ("make it realistic",       "Natural matte surface"),
            ("make it cartoon",         "Flat bright orange"),
            ("make it metallic",        "Chrome mirror finish"),
            ("make it glowing",         "Green emission glow"),
            ("make it wooden",          "Brown rough wood"),
            ("make it stone",           "Grey rough stone"),
            ("make it glass",           "Transparent refraction"),
        ]
    },
    {
        "title": "◈ PHASE 3 — AI & CLAUDE",
        "color": (255, 100, 200),
        "items": [
            ("ai material",             "Claude generates material code"),
            ("ai suggest",              "Claude reviews your mesh"),
            ("ai fix",                  "Claude fixes mesh topology"),
            ("hey claude",              "General AI assistant"),
            ("API key required",        "console.anthropic.com"),
        ]
    },
    {
        "title": "◈ PHASE 3 — MESH CREATION",
        "color": (255, 180, 0),
        "items": [
            ("create sphere",           "Add UV sphere at origin"),
            ("create cube",             "Add cube at origin"),
            ("create cylinder",         "Add cylinder at origin"),
            ("create cone",             "Add cone at origin"),
            ("create torus",            "Add torus at origin"),
            ("create plane",            "Add plane at origin"),
            ("create monkey",           "Add Suzanne at origin"),
            ("generate mesh",           "AI builds shape by description"),
        ]
    },
    {
        "title": "◈ PHASE 3 — POSE & DUAL HAND",
        "color": (255, 80, 80),
        "items": [
            ("enable pose",             "Full body controls object"),
            ("disable pose",            "Turn off body tracking"),
            ("dual hand mode",          "Left=move Right=rotate"),
            ("single hand mode",        "Back to one hand control"),
            ("Left wrist",              "Controls X/Z position"),
            ("Right wrist",             "Controls Y depth + scale"),
        ]
    },
    {
        "title": "◈ PHASE 3 — AR & BLENDERKIT",
        "color": (0, 255, 120),
        "items": [
            ("ar mode",                 "Project model on camera feed"),
            ("enable ar",               "AR overlay ON"),
            ("disable ar",              "AR overlay OFF"),
            ("blender kit <name>",      "Search BlenderKit by voice"),
            ("download asset",          "Download first result"),
            ("next asset",              "Scroll to next result"),
            ("previous asset",          "Scroll back one result"),
        ]
    },
]
# ── HUD Message Display ──────────────────────────────────────────
_hud_messages = []          # list of {text, born, duration}
_hud_msg_lock = threading.Lock()

def hud_message(text: str, duration: float = 4.0):
    """Show a message on the HUD overlay (thread-safe)."""
    with _hud_msg_lock:
        _hud_messages.append({
            "text":     text,
            "born":     time.time(),
            "duration": duration,
        })
        # Keep max 5 messages on screen
        if len(_hud_messages) > 5:
            _hud_messages.pop(0)
# ── TTS engine (macOS-safe: create once on main thread) ─────────────────────────
_tts_engine = None
_tts_lock   = threading.Lock()

def _init_tts():
    global _tts_engine
    if not TTS_OK:
        return
    try:
        _tts_engine = pyttsx3.init()
        # On macOS, 'nsss' driver works best; pyttsx3 selects it automatically.
        # Avoid runAndWait in threads — use event loop approach instead.
    except Exception as e:
        print(f"[TTS INIT ERROR] {e}")

_init_tts()

# TTS runs its own mini-loop so it never blocks the Blender timer.










# ── TTS — macOS native (replaces pyttsx3) ──────────────────────
import subprocess

_tts_queue  = Queue()
_tts_active = False

def _tts_worker():
    global _tts_active
    while True:
        text = _tts_queue.get()
        if text is None:
            break
        _tts_active = True
        try:
            subprocess.run(["say", "-r", "200", text], 
                           timeout=10, 
                           stdout=subprocess.DEVNULL, 
                           stderr=subprocess.DEVNULL)
        except Exception as e:
            print(f"[TTS ERROR] {e}")
        finally:
            _tts_active = False

_tts_thread = threading.Thread(target=_tts_worker, daemon=True)
_tts_thread.start()

def speak(text: str):
    print(f"[SPEAK] {text}")
    # Clear queue so new speech isn't blocked by a backlog
    while not _tts_queue.empty():
        try:
            _tts_queue.get_nowait()
        except:
            pass
    _tts_queue.put(text)
    hud_message(text)

def jarvis_speak(text: str):
    print(f"[JARVIS] {text}")
    speak(text)

    #-----------------------------------------------------

# ═══════════════════════════════════════════════════════════════════════════════
#  SUPPORTED IMPORTERS
# ═══════════════════════════════════════════════════════════════════════════════
IMPORTERS = {
    ".ply":  lambda p: bpy.ops.wm.ply_import(filepath=p),
    ".obj":  lambda p: bpy.ops.wm.obj_import(filepath=p),
    ".fbx":  lambda p: bpy.ops.import_scene.fbx(filepath=p),
    ".stl":  lambda p: bpy.ops.import_mesh.stl(filepath=p),
    ".glb":  lambda p: bpy.ops.import_scene.gltf(filepath=p),
    ".gltf": lambda p: bpy.ops.import_scene.gltf(filepath=p),
    ".dae":  lambda p: bpy.ops.wm.collada_import(filepath=p),
    ".usd":  lambda p: bpy.ops.wm.usd_import(filepath=p),
    ".usdz": lambda p: bpy.ops.wm.usd_import(filepath=p),
}
# ═══════════════════════════════════════════════════════════════════════════════
#  PHASE 2 — FACE MESH INIT
# ═══════════════════════════════════════════════════════════════════════════════
def _init_face_mesh():
    global _face_mesh
    if not MP_OK:
        return
    try:
        _face_mesh = mp.solutions.face_mesh.FaceMesh(
            max_num_faces=1,
            refine_landmarks=True,
            min_detection_confidence=0.6,
            min_tracking_confidence=0.5,
        )
        print("[OK] Face mesh ready — eye tracking enabled")
    except Exception as e:
        print(f"[FACE MESH INIT] {e}")
        _face_mesh = None

_init_face_mesh()
# ═══════════════════════════════════════════════════════════════════════════════
#  PHASE 3 — AI CLIENT INIT
# ═══════════════════════════════════════════════════════════════════════════════
def _init_ai():
    global _ai_client
    try:
        _ai_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        print("[OK] Claude AI client ready")
    except Exception as e:
        print(f"[AI INIT] {e}")
        _ai_client = None

_init_ai()


# ═══════════════════════════════════════════════════════════════════════════════
#  PHASE 3 — POSE DETECTOR INIT
# ═══════════════════════════════════════════════════════════════════════════════
def _init_pose():
    global _pose_detector
    if not MP_OK:
        return
    try:
        _pose_detector = mp.solutions.pose.Pose(
            min_detection_confidence=0.6,
            min_tracking_confidence=0.5,
        )
        print("[OK] Pose detector ready")
    except Exception as e:
        print(f"[POSE INIT] {e}")
        _pose_detector = None

_init_pose()
# ═══════════════════════════════════════════════════════════════════════════════
#  MEDIAPIPE + CAMERA  (lazy-init so missing deps don't crash at import)
# ═══════════════════════════════════════════════════════════════════════════════
_hands_detector = None
_cap            = None

def _init_camera():
    global _hands_detector, _cap
    if not (CV2_OK and MP_OK):
        return
    try:
        mp_hands         = mp.solutions.hands
        _hands_detector  = mp_hands.Hands(
            max_num_hands=2,
            min_detection_confidence=0.7,
            min_tracking_confidence=0.5,
        )
        _cap = cv2.VideoCapture(0)
        if not _cap.isOpened():
            print("[WARN] Camera index 0 not available — trying index 1")
            _cap = cv2.VideoCapture(1)
        if not _cap.isOpened():
            print("[WARN] No camera found — gesture control disabled")
            _cap = None
    except Exception as e:
        print(f"[CAMERA INIT ERROR] {e}")

_init_camera()


# ═══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════════════════════
def lerp(a, b, f):
    return a + (b - a) * f

def active():
    return bpy.context.active_object

def fix_object(obj):
    """Centre-origin, move to world origin, normalise scale."""
    bpy.ops.object.origin_set(type='ORIGIN_GEOMETRY')
    obj.location = (0, 0, 0)
    max_dim = max(obj.dimensions)
    if max_dim > 5:
        s = 2 / max_dim
        obj.scale = (s, s, s)
    bpy.ops.object.transform_apply(scale=True)

# ═══════════════════════════════════════════════════════════════════════════════
#  MODEL ANALYSER / GRADER
# ═══════════════════════════════════════════════════════════════════════════════
def analyze_model(obj):
    if not obj or obj.type != 'MESH':
        return None

    mesh = obj.data
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()

    verts         = len(bm.verts)
    edges         = len(bm.edges)
    faces         = len(bm.faces)
    tris          = sum(1 for f in bm.faces if len(f.verts) == 3)
    quads         = sum(1 for f in bm.faces if len(f.verts) == 4)
    ngons         = sum(1 for f in bm.faces if len(f.verts) > 4)
    non_manifold_e = sum(1 for e in bm.edges if not e.is_manifold)
    non_manifold_v = sum(1 for v in bm.verts if not v.is_manifold)
    has_uv        = len(mesh.uv_layers) > 0
    has_mat       = len(obj.data.materials) > 0
    max_dim       = max(obj.dimensions)
    bm.free()

    score  = 100
    issues = []

    def penalise(pts, msg):
        nonlocal score
        score -= pts
        issues.append(f"{msg} (−{pts} pts)")

    if ngons > 0:          penalise(min(30, ngons * 2),         f"{ngons} N-gons detected")
    if non_manifold_e > 0: penalise(min(25, non_manifold_e * 3), f"{non_manifold_e} non-manifold edges")
    if non_manifold_v > 0: penalise(min(15, non_manifold_v * 2), f"{non_manifold_v} non-manifold vertices")
    if not has_uv:          penalise(10,                          "No UV map found")
    if not has_mat:         penalise(5,                           "No material assigned")
    if verts > 500_000:     penalise(15,                          f"Very high poly count: {verts:,}")
    elif verts > 100_000:   penalise(5,                           f"High poly count: {verts:,}")
    if max_dim > 50 or max_dim < 0.01:
        penalise(10, f"Unusual scale: {max_dim:.3f} units")

    score = max(0, score)
    grade = "S" if score >= 90 else "A" if score >= 80 else "B" if score >= 70 \
          else "C" if score >= 55 else "D" if score >= 40 else "F"

    report = dict(name=obj.name, verts=verts, edges=edges, faces=faces,
                  tris=tris, quads=quads, ngons=ngons,
                  non_manifold_edges=non_manifold_e,
                  non_manifold_verts=non_manifold_v,
                  has_uv=has_uv, has_material=has_mat,
                  max_dimension=max_dim, score=score, grade=grade, issues=issues)
    analysis_cache[obj.name] = report
    return report

def speak_analysis(report):
    if not report:
        jarvis_speak("No valid mesh object selected, sir.")
        return
    r = report
    jarvis_speak(
        f"Analysis complete. {r['name']} scores {r['score']} out of 100. "
        f"Grade: {r['grade']}. {r['verts']:,} vertices, {r['faces']:,} faces. "
        f"{r['ngons']} N-gons, {r['non_manifold_edges']} non-manifold edges. "
        f"{'UV map present.' if r['has_uv'] else 'No UV map detected.'} "
        f"{'Material assigned.' if r['has_material'] else 'No material found.'} "
        f"{len(r['issues'])} issues total."
    )

# ═══════════════════════════════════════════════════════════════════════════════
#  JARVIS ACTION EXECUTOR
# ═══════════════════════════════════════════════════════════════════════════════
def execute_jarvis_action(action: str, params: dict, obj):
    try:
        if action == "ANALYZE_MODEL":
            speak_analysis(analyze_model(obj))

        elif action == "AUTO_FIX":
            if obj and obj.type == 'MESH':
                bpy.context.view_layer.objects.active = obj
                bpy.ops.object.mode_set(mode='EDIT')
                bpy.ops.mesh.select_all(action='SELECT')
                bpy.ops.mesh.remove_doubles(threshold=0.001)
                bpy.ops.mesh.fill_holes(sides=4)
                bpy.ops.mesh.normals_make_consistent(inside=False)
                bpy.ops.object.mode_set(mode='OBJECT')
                jarvis_speak("Auto-fix complete. Removed doubles, filled holes, corrected normals.")

        elif action == "SET_MATERIAL":
            if obj:
                mat  = bpy.data.materials.new("JarvisMat")
                mat.use_nodes = True
                bsdf = mat.node_tree.nodes["Principled BSDF"]
                c    = params.get("color", [0.0, 0.8, 1.0])
                bsdf.inputs[0].default_value  = (c[0], c[1], c[2], 1)
                bsdf.inputs[6].default_value  = params.get("metallic",  0.9)
                bsdf.inputs[9].default_value  = params.get("roughness", 0.1)
                # Emission strength (index varies by Blender version — use name lookup)
                em_node = bsdf.inputs.get("Emission Strength")
                if em_node:
                    em_node.default_value = params.get("emission", 0.3)
                if not obj.data.materials:
                    obj.data.materials.append(mat)
                else:
                    obj.data.materials[0] = mat
                jarvis_speak("Material applied, sir.")

        elif action == "SETUP_LIGHTING":
            style = params.get("style", "cinematic")
            for o in list(bpy.data.objects):
                if o.type == 'LIGHT':
                    bpy.data.objects.remove(o, do_unlink=True)

            configs = {
                "cinematic": [
                    ("Key",    "SPOT",  (4, 4, 4),   2000, (1, .95, .8)),
                    ("Fill",   "AREA",  (-3, 2, 2),   200, (.5, .6, 1)),
                    ("Rim",    "SPOT",  (-2, -4, 3),  500, (1, .8, .6)),
                ],
                "sci-fi": [
                    ("Cyan",   "POINT", (0, 3, 4),    800, (0, 1, .9)),
                    ("Purple", "POINT", (3, -2, 2),   600, (.6, 0, 1)),
                    ("Blue",   "AREA",  (-3, 0, 5),   400, (0, .4, 1)),
                ],
                "studio": [
                    ("Main",   "AREA",  (3, 3, 5),   1000, (1, 1, 1)),
                    ("Fill",   "AREA",  (-3, 1, 3),   400, (1, 1, 1)),
                    ("Back",   "AREA",  (0, -4, 2),   300, (1, 1, 1)),
                ],
                "dramatic": [
                    ("Hard",   "SPOT",  (5, 0, 6),   3000, (1, .9, .7)),
                    ("Accent", "POINT", (-2, -3, 1),  200, (1, .2, .1)),
                ],
            }
            for name, ltype, loc, energy, col in configs.get(style, configs["cinematic"]):
                bpy.ops.object.light_add(type=ltype, location=loc)
                l = bpy.context.active_object
                l.name         = name
                l.data.energy  = energy
                l.data.color   = col
            jarvis_speak(f"{style.capitalize()} lighting configured.")

        elif action == "DECIMATE_MODEL":
            if obj and obj.type == 'MESH':
                mod       = obj.modifiers.new("JarvisDecimate", "DECIMATE")
                mod.ratio = params.get("ratio", 0.5)
                bpy.ops.object.modifier_apply(modifier="JarvisDecimate")
                jarvis_speak(f"Model decimated to {int(params.get('ratio', 0.5)*100)} percent.")

        elif action == "SMOOTH_MODEL":
            if obj:
                bpy.ops.object.shade_smooth()
                mod        = obj.modifiers.new("JarvisSubDiv", "SUBSURF")
                mod.levels = 2
                jarvis_speak("Smooth shading and subdivision applied.")

        elif action == "AUTO_UV":
            if obj and obj.type == 'MESH':
                bpy.context.view_layer.objects.active = obj
                bpy.ops.object.mode_set(mode='EDIT')
                bpy.ops.mesh.select_all(action='SELECT')
                bpy.ops.uv.smart_project(angle_limit=66)
                bpy.ops.object.mode_set(mode='OBJECT')
                jarvis_speak("Smart UV projection complete.")

        elif action == "SCALE_NORMALIZE":
            if obj:
                fix_object(obj)
                jarvis_speak("Model normalised to standard scale.")

        elif action == "WIREFRAME_TOGGLE":
            for area in bpy.context.screen.areas:
                if area.type == 'VIEW_3D':
                    area.spaces[0].overlay.show_wireframes ^= True
            jarvis_speak("Wireframe toggled.")

        elif action == "SET_RENDER_ENGINE":
            eng = params.get("engine", "EEVEE")
            bpy.context.scene.render.engine = "BLENDER_EEVEE" if eng == "EEVEE" else "CYCLES"
            jarvis_speak(f"Render engine set to {eng}.")

        elif action == "APPLY_MODIFIER":
            if obj:
                mtype = params.get("type", "BEVEL")
                mod   = obj.modifiers.new(f"J_{mtype}", mtype)
                val   = params.get("value", 0.05)
                if mtype == "BEVEL":    mod.width     = val
                elif mtype == "SOLIDIFY": mod.thickness = val
                elif mtype == "ARRAY":  mod.count     = int(val)
                jarvis_speak(f"{mtype.capitalize()} modifier applied.")

        elif action == "ENVIRONMENT_SETUP":
            world = bpy.context.scene.world
            if not world:
                world = bpy.data.worlds.new("JarvisWorld")
                bpy.context.scene.world = world
            world.use_nodes = True
            bg = world.node_tree.nodes.get("Background")
            if bg:
                bg.inputs[0].default_value = (0.0, 0.02, 0.05, 1)
                bg.inputs[1].default_value = 0.3
            jarvis_speak("Sci-fi environment configured.")

        elif action == "EXPORT_MODEL":
            if obj:
                fmt  = params.get("format", "glb")
                path = os.path.join(MODEL_FOLDER, f"{obj.name}_export.{fmt}")
                if fmt == "glb":  bpy.ops.export_scene.gltf(filepath=path)
                elif fmt == "fbx": bpy.ops.export_scene.fbx(filepath=path)
                elif fmt == "obj": bpy.ops.wm.obj_export(filepath=path)
                jarvis_speak(f"Model exported as {fmt}.")

        elif action == "REPORT_ONLY":
            pass  # speech already handled by caller

    except Exception as e:
        print(f"[JARVIS ACTION ERROR] {e}")
        jarvis_speak("I encountered an issue executing that command, sir.")
# ═══════════════════════════════════════════════════════════════════════════════
#  PHASE 1 — HUD OVERLAYS
# ═══════════════════════════════════════════════════════════════════════════════

def _draw_fps_counter(frame, t):
    """Live FPS counter — top left of HUD bar."""
    now = time.time()
    _fps_times.append(now)
    if len(_fps_times) >= 2:
        elapsed = _fps_times[-1] - _fps_times[0]
        fps = (len(_fps_times) - 1) / elapsed if elapsed > 0 else 0.0
    else:
        fps = 0.0

    h, w = frame.shape[:2]
    C    = _mood_color

    # Color code: green=good, yellow=ok, red=bad
    if fps >= 25:   col = (0, 220, 80)
    elif fps >= 15: col = (0, 200, 220)
    else:           col = (0, 60, 255)

    cv2.putText(frame, f"FPS {fps:04.1f}",
                (w - 310, 28),
                cv2.FONT_HERSHEY_SIMPLEX, 0.44, col, 1)


def _draw_poly_counter(frame, obj, t):
    """Live polygon / vertex counter for active object."""
    h, w = frame.shape[:2]
    C    = _mood_color

    if obj and obj.type == 'MESH':
        verts = len(obj.data.vertices)
        faces = len(obj.data.polygons)
        tris  = sum(p.loop_total - 2 for p in obj.data.polygons)

        # Panel — sits just below the top bar on the left
        px, py = 28, 50
        pw, ph = 190, 68

        overlay = frame.copy()
        cv2.rectangle(overlay, (px, py), (px+pw, py+ph), (0, 0, 0), -1)
        cv2.addWeighted(overlay, 0.55, frame, 0.45, 0, frame)
        cv2.rectangle(frame, (px, py), (px+pw, py+ph), C, 1)
        cv2.line(frame, (px, py+18), (px+pw, py+18), (0, 60, 40), 1)

        cv2.putText(frame, "◈ MESH STATS",
                    (px+6, py+13),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.32, C, 1)

        stats = [
            (f"VERTS  {verts:>8,}", (0, 200, 140)),
            (f"FACES  {faces:>8,}", (0, 180, 255)),
            (f"TRIS   {tris:>8,}",  (180, 100, 255)),
        ]
        for i, (txt, col) in enumerate(stats):
            cv2.putText(frame, txt,
                        (px+8, py+32+i*16),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.33, col, 1)
    else:
        # No mesh selected
        cv2.putText(frame, "◈ NO MESH",
                    (28, 65),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.34, (0, 80, 60), 1)


def _draw_command_log(frame, t):
    """Last 5 voice commands shown on HUD — bottom left above messages."""
    if not _command_log:
        return

    h, w  = frame.shape[:2]
    C     = _mood_color
    now   = time.time()

    px, py = 28, h - 240
    pw, ph = 220, len(_command_log) * 20 + 24

    overlay = frame.copy()
    cv2.rectangle(overlay, (px, py), (px+pw, py+ph), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.5, frame, 0.5, 0, frame)
    cv2.rectangle(frame, (px, py), (px+pw, py+ph), C, 1)
    cv2.line(frame, (px, py+18), (px+pw, py+18), (0, 60, 40), 1)

    cv2.putText(frame, "◈ COMMAND LOG",
                (px+6, py+13),
                cv2.FONT_HERSHEY_SIMPLEX, 0.32, C, 1)

    for i, (cmd, ts) in enumerate(reversed(_command_log)):
        age        = now - ts
        brightness = max(60, int(220 - age * 30))
        col        = (0, brightness, int(brightness * 0.7))
        prefix     = "▶" if i == 0 else " "
        cv2.putText(frame,
                    f"{prefix} {cmd[0][:22]}",
                    (px+8, py+30+i*20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.32, col, 1)


def _draw_status_ticker(frame, t):
    """Cycling sci-fi status messages on the bottom bar."""
    global _status_index, _status_timer

    now = time.time()
    if now - _status_timer > _status_interval:
        _status_index = (_status_index + 1) % len(_status_messages)
        _status_timer = now

    h, w  = frame.shape[:2]
    msg   = _status_messages[_status_index]
    C     = _mood_color

    # Typewriter reveal effect
    cycle_age  = now - _status_timer
    reveal     = min(1.0, cycle_age / 0.6)
    chars      = max(1, int(len(msg) * reveal))
    display    = msg[:chars]
    if reveal < 1.0 and int(t * 12) % 2 == 0:
        display += "█"

    # Pulse brightness
    brightness = int(120 + 60 * abs(math.sin(t * 0.8)))
    col        = (0, brightness, int(brightness * 0.65))

    cv2.putText(frame, f"◈ {display}",
                (w // 2 + 60, h - 28),
                cv2.FONT_HERSHEY_SIMPLEX, 0.36, col, 1)


def _draw_mood_border(frame, t):
    """Subtle pulsing border around entire frame in mood color."""
    h, w = frame.shape[:2]
    C    = _mood_color
    pulse = int(1 + math.sin(t * 3))

    # Top and bottom mood lines (replace static green lines)
    cv2.rectangle(frame, (0, 44), (w, 46), C, -1)
    cv2.rectangle(frame, (0, h-50), (w, h-48), C, -1)
# ═══════════════════════════════════════════════════════════════════════════════
#  COMMAND PROCESSOR  (called from Blender timer — main thread)
# ═══════════════════════════════════════════════════════════════════════════════
def process_commands():
    global running, last_command_time, _menu_visible, _menu_anim_start, _menu_page

    if time.time() - last_command_time < COMMAND_COOLDOWN:
        return True

    try:
        cmd, data = command_queue.get_nowait()
    except Empty:
        return True

    last_command_time = time.time()
    _command_log.append((cmd, time.time()))
    _set_mood(cmd)

    try:
        if cmd == "IMPORT":
            if not os.path.isdir(MODEL_FOLDER):
                speak("Model folder not found")
                return True
            found = False
            for f in os.listdir(MODEL_FOLDER):
                name, ext = os.path.splitext(f)
                if name.lower() == data.lower() and ext.lower() in IMPORTERS:
                    filepath = os.path.join(MODEL_FOLDER, f)
                    IMPORTERS[ext.lower()](filepath)
                    obj = active()
                    if obj:
                        obj.name = data.capitalize()
                        fix_object(obj)
                    speak(f"Imported {data}")
                    found = True
                    break
            if not found:
                speak("File not found")

        elif cmd == "SELECT":
            matched = False
            for o in bpy.data.objects:
                if data.lower() in o.name.lower():
                    bpy.ops.object.select_all(action='DESELECT')
                    o.select_set(True)
                    bpy.context.view_layer.objects.active = o
                    speak(f"Selected {o.name}")
                    matched = True
                    break
            if not matched:
                speak("Not found")

        elif cmd == "SCALE_LOCAL":
            obj = active()
            if obj:
                factor = data
                obj.scale = (
                    obj.scale.x * factor,
                    obj.scale.y * factor,
                    obj.scale.z * factor,
                )
                speak(f"Scale {'increased' if factor > 1 else 'decreased'}")

        elif cmd == "DELETE":
            obj = active()
            if obj:
                speak(f"Deleted {obj.name}")
                bpy.ops.object.delete()

        elif cmd == "GESTURE":
            obj = active()
            if obj:
                step = 0.5
                if data == "left":      obj.location.x -= step
                elif data == "right":   obj.location.x += step
                elif data == "up":      obj.location.z += step
                elif data == "down":    obj.location.z -= step
                elif data == "forward": obj.location.y += step
                elif data == "back":    obj.location.y -= step
                speak(f"Move {data}")

        elif cmd == "COLOR":
            # ── FIXED: single handler using _apply_material ──────
            obj = active()
            if obj and obj.type == 'MESH':
                _apply_material(obj, data)
            else:
                speak("No mesh selected.")

        elif cmd == "JARVIS_ACTION":
            # ── FIXED: renamed to avoid 'action' conflict ─────────
            jarvis_action, jarvis_params, jarvis_obj = data
            execute_jarvis_action(jarvis_action, jarvis_params, jarvis_obj)

        elif cmd == "MENU_TOGGLE":
            _menu_visible    = not _menu_visible
            _menu_anim_start = time.time()
            _menu_page       = 0
            if _menu_visible:
                speak("Help menu open. Say next page or previous page.")
            else:
                speak("Menu closed.")

        elif cmd == "MENU_NEXT":
            _menu_page = (_menu_page + 1) % len(MENU_PAGES)
            speak(f"Page {_menu_page + 1}. {MENU_PAGES[_menu_page]['title'].replace('◈ ', '')}")

        elif cmd == "MENU_PREV":
            _menu_page = (_menu_page - 1) % len(MENU_PAGES)
            speak(f"Page {_menu_page + 1}. {MENU_PAGES[_menu_page]['title'].replace('◈ ', '')}")

        elif cmd == "MENU_CLOSE":
            _menu_visible = False
            speak("Menu closed.")

        elif cmd == "UNDO":
            bpy.ops.ed.undo()
            speak("Undone.")

        elif cmd == "REDO":
            bpy.ops.ed.redo()
            speak("Redone.")

        elif cmd == "SCREENSHOT":
            try:
                os.makedirs(SCREENSHOT_FOLDER, exist_ok=True)
                fname = os.path.join(
                    SCREENSHOT_FOLDER,
                    f"shot_{time.strftime('%Y%m%d_%H%M%S')}.png")
                bpy.context.scene.render.filepath = fname
                bpy.ops.render.opengl(write_still=True)
                speak("Screenshot saved.")
                hud_message(f"◈ SAVED: {os.path.basename(fname)}", duration=4.0)
            except Exception as e:
                print(f"[SCREENSHOT ERROR] {e}")
                speak("Screenshot failed.")

        elif cmd == "MIRROR":
            obj = active()
            if obj and obj.type == 'MESH':
                mod = obj.modifiers.new("VoiceMirror", "MIRROR")
                mod.use_axis[0] = (data == "X")
                mod.use_axis[1] = (data == "Y")
                mod.use_axis[2] = (data == "Z")
                speak(f"Mirror {data} applied.")
            else:
                speak("No mesh selected.")

        elif cmd == "ARRAY":
            obj = active()
            if obj and obj.type == 'MESH':
                mod       = obj.modifiers.new("VoiceArray", "ARRAY")
                mod.count = data
                speak(f"Array of {data} created.")
            else:
                speak("No mesh selected.")

        elif cmd == "SNAP":
            obj = active()
            if obj:
                obj.location.x = round(obj.location.x)
                obj.location.y = round(obj.location.y)
                obj.location.z = round(obj.location.z)
                speak("Snapped to grid.")

        elif cmd == "SAVE":
            if bpy.data.filepath:
                bpy.ops.wm.save_mainfile(filepath="/Users/harinathrayala/Desktop/plymodel/autosave.blend")
                speak("File saved.")
                hud_message("✔ FILE SAVED", duration=3.0)
            else:
                speak("Please save the file manually first.")

        elif cmd == "SCENE_LIST":
            names = [o.name for o in bpy.data.objects]
            hud_message("SCENE: " + "  |  ".join(names), duration=6.0)
            speak(f"{len(names)} objects in scene.")

        elif cmd == "VIEWPORT_MODE":
            global _viewport_mode
            if data == "toggle":
                _viewport_mode = not _viewport_mode
            else:
                _viewport_mode = bool(data)
            mode_label = "VIEWPORT" if _viewport_mode else "OBJECT"
            speak(f"Hand now controls {mode_label}")
            hud_message(f"◈ HAND MODE: {mode_label}", duration=3.0)

        elif cmd == "TIMELINE":
            try:
                scene = bpy.context.scene
                if data == "play":
                    bpy.ops.screen.animation_play()
                    speak("Playing animation")
                elif data == "stop":
                    bpy.ops.screen.animation_cancel(restore_frame=False)
                    speak("Animation stopped")
                elif data == "rewind":
                    scene.frame_current = scene.frame_start
                    speak("Rewound to start")
                elif data == "next":
                    scene.frame_current = min(scene.frame_current + 1, scene.frame_end)
                    speak(f"Frame {scene.frame_current}")
                elif data == "prev":
                    scene.frame_current = max(scene.frame_current - 1, scene.frame_start)
                    speak(f"Frame {scene.frame_current}")
                elif data == "start":
                    scene.frame_current = scene.frame_start
                    speak(f"Frame {scene.frame_start}")
                elif data == "end":
                    scene.frame_current = scene.frame_end
                    speak(f"Frame {scene.frame_end}")
                hud_message(f"◈ FRAME {bpy.context.scene.frame_current}", duration=2.0)
            except Exception as e:
                print(f"[TIMELINE] {e}")
                speak("Timeline error")

        elif cmd == "FACE_TRACK":
            global _face_tracking_on
            if data == "toggle":
                _face_tracking_on = not _face_tracking_on
            else:
                _face_tracking_on = bool(data)
            state = "ON" if _face_tracking_on else "OFF"
            speak(f"Face tracking {state}")
            hud_message(f"◈ FACE TRACKING: {state}", duration=3.0)

        elif cmd == "AI_QUICK":
            obj = active()
            _apply_quick_preset(data, obj)

        elif cmd == "AI_PROMPT":
            obj = active()
            if data == "material":
                prompt = _build_material_prompt(obj, "futuristic sci-fi")
                def _on_mat(code): command_queue.put(("_EXEC_CODE", code))
                _ai_call_async(prompt, _on_mat)
                speak("Asking Claude for a material")
            elif data == "suggest":
                name   = obj.name if obj else "unknown"
                cached = analysis_cache.get(name, {})
                prompt = (f"Blender mesh '{name}', score {cached.get('score','unknown')}. "
                          f"Issues: {cached.get('issues',[])}. "
                          f"Give 3 short actionable suggestions. Plain text.")
                def _on_suggest(text):
                    speak(text[:200])
                    hud_message(text[:80], duration=8.0)
                _ai_call_async(prompt, _on_suggest)
                speak("Asking Claude for suggestions")
            elif data == "fix":
                name   = obj.name if obj else "mesh"
                prompt = _build_mesh_prompt(f"improved version of {name}")
                def _on_fix(code): command_queue.put(("_EXEC_CODE", code))
                _ai_call_async(prompt, _on_fix)
                speak("Asking Claude to fix mesh")

        elif cmd == "_EXEC_CODE":
            _execute_ai_code(data)

        elif cmd == "VOICE_MESH":
            if data == "ai":
                speak("Describe the shape you want")
                hud_message("◈ SPEAK SHAPE DESCRIPTION", duration=4.0)
            else:
                ops = {
                    "sphere":   lambda: bpy.ops.mesh.primitive_uv_sphere_add(location=(0,0,0)),
                    "cube":     lambda: bpy.ops.mesh.primitive_cube_add(location=(0,0,0)),
                    "cylinder": lambda: bpy.ops.mesh.primitive_cylinder_add(location=(0,0,0)),
                    "cone":     lambda: bpy.ops.mesh.primitive_cone_add(location=(0,0,0)),
                    "torus":    lambda: bpy.ops.mesh.primitive_torus_add(location=(0,0,0)),
                    "plane":    lambda: bpy.ops.mesh.primitive_plane_add(location=(0,0,0)),
                    "monkey":   lambda: bpy.ops.mesh.primitive_monkey_add(location=(0,0,0)),
                }
                if data in ops:
                    ops[data]()
                    speak(f"Created {data}")
                    hud_message(f"◈ CREATED: {data.upper()}", duration=3.0)

        elif cmd == "POSE_TRACK":
            global _pose_tracking_on
            _pose_tracking_on = bool(data)
            state = "ON" if _pose_tracking_on else "OFF"
            speak(f"Pose tracking {state}")
            hud_message(f"◈ POSE: {state}", duration=3.0)

        elif cmd == "DUAL_HAND":
            global _dual_hand_mode
            _dual_hand_mode = bool(data)
            state = "ON" if _dual_hand_mode else "OFF"
            speak(f"Dual hand mode {state}")
            hud_message(f"◈ DUAL HAND: {state}", duration=3.0)

        elif cmd == "AR_MODE":
            global _ar_mode
            if data == "toggle":
                _ar_mode = not _ar_mode
            else:
                _ar_mode = bool(data)
            state = "ON" if _ar_mode else "OFF"
            speak(f"AR mode {state}")
            hud_message(f"◈ AR: {state}", duration=3.0)

        elif cmd == "QUIT":
            running = False
            speak("System shutting down")
            _shutdown()
            return None

    except Exception as e:
        print(f"[COMMAND ERROR] {e}")

    return True
# ═══════════════════════════════════════════════════════════════════════════════
#  PHASE 2 — EYE TRACKING
# ═══════════════════════════════════════════════════════════════════════════════
# MediaPipe face mesh landmark indices for iris centres
_LEFT_IRIS  = [474, 475, 476, 477]
_RIGHT_IRIS = [469, 470, 471, 472]

def _process_face(frame, rgb):
    """
    Run face mesh on current frame.
    Returns (eye_x, eye_y) normalised 0-1, or None if no face.
    Updates _eye_smooth_x / _eye_smooth_y globals.
    """
    global _eye_smooth_x, _eye_smooth_y, _eye_cursor_x, _eye_cursor_y

    if not _face_tracking_on or _face_mesh is None:
        return None

    try:
        result = _face_mesh.process(rgb)
        if not result.multi_face_landmarks:
            return None

        lm = result.multi_face_landmarks[0].landmark

        # Average left + right iris positions
        lx = sum(lm[i].x for i in _LEFT_IRIS)  / len(_LEFT_IRIS)
        ly = sum(lm[i].y for i in _LEFT_IRIS)  / len(_LEFT_IRIS)
        rx = sum(lm[i].x for i in _RIGHT_IRIS) / len(_RIGHT_IRIS)
        ry = sum(lm[i].y for i in _RIGHT_IRIS) / len(_RIGHT_IRIS)

        raw_x = (lx + rx) / 2
        raw_y = (ly + ry) / 2

        # Smooth with lerp
        _eye_smooth_x = lerp(_eye_smooth_x, raw_x, 0.15)
        _eye_smooth_y = lerp(_eye_smooth_y, raw_y, 0.15)

        _eye_cursor_x = _eye_smooth_x
        _eye_cursor_y = _eye_smooth_y

        return (_eye_smooth_x, _eye_smooth_y)

    except Exception as e:
        print(f"[EYE TRACK] {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════════════
#  PHASE 2 — VIEWPORT HAND CONTROL
# ═══════════════════════════════════════════════════════════════════════════════
def _process_viewport_gesture(hand_landmarks, obj):
    """
    When _viewport_mode is True, move the hand to orbit
    the Blender 3D viewport instead of moving the object.
    """
    global _last_hand_vp_x, _last_hand_vp_y
    global _viewport_rot_x, _viewport_rot_z

    if not _viewport_mode or hand_landmarks is None:
        _last_hand_vp_x = None
        _last_hand_vp_y = None
        return

    try:
        lm   = hand_landmarks.landmark
        cx   = lm[9].x
        cy   = lm[9].y

        if _last_hand_vp_x is not None:
            dx = cx - _last_hand_vp_x
            dy = cy - _last_hand_vp_y

            _viewport_rot_z += dx * 180
            _viewport_rot_x += dy * 90

            # Apply rotation to all 3D viewports
            for area in bpy.context.screen.areas:
                if area.type == 'VIEW_3D':
                    r3d = area.spaces[0].region_3d
                    import mathutils
                    # Orbit around scene
                    rot_z = mathutils.Matrix.Rotation(
                        math.radians(dx * 180), 4, 'Z')
                    rot_x = mathutils.Matrix.Rotation(
                        math.radians(dy * 90), 4, 'X')
                    r3d.view_matrix = (
                        rot_x @ rot_z @ r3d.view_matrix)
                    break

        _last_hand_vp_x = cx
        _last_hand_vp_y = cy

    except Exception as e:
        print(f"[VIEWPORT GESTURE] {e}")
        _last_hand_vp_x = None
        _last_hand_vp_y = None
# ═══════════════════════════════════════════════════════════════════════════════
#  PHASE 2 — HUD OVERLAYS
# ═══════════════════════════════════════════════════════════════════════════════
def _draw_eye_cursor(frame, t):
    """Draw eye-tracking cursor on the HUD."""
    if not _face_tracking_on:
        return

    h, w = frame.shape[:2]
    cx   = int(_eye_cursor_x * w)
    cy   = int(_eye_cursor_y * h)
    C    = (0, 220, 255)

    # Outer ring
    cv2.circle(frame, (cx, cy), 18, C, 1)
    # Inner dot
    cv2.circle(frame, (cx, cy),  3, C, -1)
    # Crosshair lines
    cv2.line(frame, (cx-12, cy), (cx-5, cy),  C, 1)
    cv2.line(frame, (cx+5,  cy), (cx+12, cy), C, 1)
    cv2.line(frame, (cx, cy-12), (cx, cy-5),  C, 1)
    cv2.line(frame, (cx, cy+5),  (cx, cy+12), C, 1)
    # Pulsing outer glow
    pulse = int(22 + 4 * math.sin(t * 6))
    cv2.circle(frame, (cx, cy), pulse,
               (0, int(80 + 40*math.sin(t*6)), 100), 1)

    cv2.putText(frame, "EYE",
                (cx + 22, cy + 4),
                cv2.FONT_HERSHEY_SIMPLEX, 0.28,
                (0, 140, 180), 1)


def _draw_timeline_hud(frame, t):
    """Timeline / animation info bar."""
    h, w = frame.shape[:2]
    C    = _mood_color

    try:
        scene   = bpy.context.scene
        current = scene.frame_current
        start   = scene.frame_start
        end     = scene.frame_end
        total   = max(1, end - start)
        progress = (current - start) / total

        # Bar position — just above bottom bar
        bx, by = 28, h - 68
        bw     = w - 56
        bh     = 10

        # Background
        cv2.rectangle(frame, (bx, by), (bx+bw, by+bh),
                      (0, 20, 14), -1)
        # Progress fill
        fill_w = int(bw * progress)
        cv2.rectangle(frame, (bx, by), (bx+fill_w, by+bh),
                      C, -1)
        # Border
        cv2.rectangle(frame, (bx, by), (bx+bw, by+bh),
                      (0, 100, 70), 1)

        # Playhead marker
        ph_x = bx + fill_w
        cv2.line(frame, (ph_x, by-4), (ph_x, by+bh+4), (0, 255, 255), 1)

        # Frame counter
        cv2.putText(frame,
                    f"◈ FRAME  {current} / {end}",
                    (bx, by - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.34, C, 1)

        # Playing indicator
        if bpy.context.screen.is_animation_playing:
            if int(t * 4) % 2 == 0:
                cv2.putText(frame, "▶ PLAYING",
                            (bx + bw - 80, by - 6),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.34,
                            (0, 255, 120), 1)

    except Exception as e:
        pass   # silently skip if no scene context


def _draw_viewport_mode_indicator(frame, t):
    """Show which mode the hand is controlling."""
    h, w  = frame.shape[:2]
    C     = (0, 255, 180) if not _viewport_mode else (255, 200, 0)
    label = "HAND → OBJECT" if not _viewport_mode else "HAND → VIEWPORT"

    pulse = int(180 + 60 * abs(math.sin(t * 3)))
    col   = (0, pulse, int(pulse * 0.7)) if not _viewport_mode \
            else (0, int(pulse * 0.8), pulse)

    cv2.putText(frame, f"◈ {label}",
                (w // 2 - 80, 28),
                cv2.FONT_HERSHEY_SIMPLEX, 0.38, col, 1)


def _draw_alert_hud(frame, t):
    """Show Blender alerts / errors on HUD."""
    global _last_alert, _alert_time

    # Hook into Blender's info log for recent reports
    try:
        for area in bpy.context.screen.areas:
            if area.type == 'INFO':
                for region in area.regions:
                    if region.type == 'WINDOW':
                        pass   # info log access varies by version
    except Exception:
        pass

    # Show last alert if recent
    if _last_alert and time.time() - _alert_time < 5.0:
        h, w  = frame.shape[:2]
        age   = time.time() - _alert_time
        alpha = max(0, 1.0 - age / 5.0)
        col   = (0, int(60 * alpha), int(200 * alpha))

        cv2.putText(frame,
                    f"⚠ {_last_alert[:40]}",
                    (28, 115),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, col, 1)


def set_alert(msg: str):
    """Call this anywhere to push an alert to the HUD."""
    global _last_alert, _alert_time
    _last_alert = msg
    _alert_time = time.time()
    hud_message(f"⚠ {msg}", duration=5.0)
# ═══════════════════════════════════════════════════════════════════════════════
#  SHUTDOWN HELPER
# ═══════════════════════════════════════════════════════════════════════════════
def _shutdown():
    """Release camera and close OpenCV windows cleanly."""
    if _cap and CV2_OK:
        try:
            _cap.release()
        except Exception:
            pass
    if CV2_OK:
        try:
            cv2.destroyAllWindows()
        except Exception:
            pass
    _tts_queue.put(None)   # stop TTS worker
    print("[INFO] Quantum Blender Controller shut down.")

# ═══════════════════════════════════════════════════════════════════════════════
#  PHASE 1 — MOOD LIGHTING SYSTEM
# ═══════════════════════════════════════════════════════════════════════════════
# Maps command types to HUD accent colors (BGR for OpenCV)
MOOD_COLORS = {
    "DELETE":        (0,   60,  255),   # red    — danger
    "QUIT":          (0,   60,  255),   # red
    "IMPORT":        (255, 180, 0),     # cyan   — loading
    "BKIT_SEARCH":   (255, 200, 0),     # cyan
    "BKIT_DOWNLOAD": (255, 220, 0),     # bright cyan
    "COLOR":         (180, 0,   255),   # purple — creative
    "JARVIS_ACTION": (0,   255, 120),   # green  — AI active
    "GESTURE":       (255, 255, 0),     # yellow — movement
    "SCALE_LOCAL":   (255, 140, 0),     # orange — transform
    "MIRROR":        (200, 80,  255),   # violet
    "ARRAY":         (200, 80,  255),   # violet
    "SCREENSHOT":    (0,   255, 255),   # white-cyan
    "UNDO":          (80,  80,  255),   # blue   — history
    "REDO":          (80,  80,  255),   # blue
    "MENU_TOGGLE":   (0,   220, 255),   # default cyan
}

def _set_mood(cmd_type: str):
    """Set HUD mood color based on last command."""
    global _mood_target
    _mood_target = MOOD_COLORS.get(cmd_type, (0, 255, 180))

def _update_mood():
    """Smoothly lerp current mood color toward target. Call every frame."""
    global _mood_color
    r = int(lerp(_mood_color[0], _mood_target[0], 0.06))
    g = int(lerp(_mood_color[1], _mood_target[1], 0.06))
    b = int(lerp(_mood_color[2], _mood_target[2], 0.06))
    _mood_color = (r, g, b)


# ═══════════════════════════════════════════════════════
#  LOCAL COMMAND MAP  — add/edit phrases here freely
# ═══════════════════════════════════════════════════════
LOCAL_COMMANDS = {
    # colours
    "red":              ("COLOR", "red"),
    "green":            ("COLOR", "green"),
    "blue":             ("COLOR", "blue"),
    "yellow":           ("COLOR", "yellow"),
    "white":            ("COLOR", "white"),
    "black":            ("COLOR", "black"),
    "orange":           ("COLOR", "orange"),
    "purple":           ("COLOR", "purple"),
    "pink":             ("COLOR", "pink"),
    "cyan":             ("COLOR", "cyan"),
    "gold":             ("COLOR", "gold"),
    "copper":           ("COLOR", "copper"),

    # style presets
    "make it futuristic":  ("COLOR", "futuristic"),
    "make it realistic":   ("COLOR", "realistic"),
    "make it cartoon":     ("COLOR", "cartoon"),
    "make it metallic":    ("COLOR", "metallic"),
    "make it glowing":     ("COLOR", "glowing"),
    "make it wooden":      ("COLOR", "wooden"),
    "make it stone":       ("COLOR", "stone"),
    "make it glass":       ("COLOR", "glass"),
    "make it gold":        ("COLOR", "gold"),
    "make it rubber":      ("COLOR", "rubber"),
    "make it plastic":     ("COLOR", "plastic"),
    # ── Phase 3 voice commands ───────────────────────────────────────
"hey claude":            ("AI_PROMPT",   "general"),
"ai material":           ("AI_PROMPT",   "material"),
"ai lighting":           ("AI_PROMPT",   "lighting"),
"ai fix":                ("AI_PROMPT",   "fix"),
"ai suggest":            ("AI_PROMPT",   "suggest"),
"make it futuristic":    ("AI_QUICK",    "futuristic"),
"make it realistic":     ("AI_QUICK",    "realistic"),
"make it cartoon":       ("AI_QUICK",    "cartoon"),
"make it metallic":      ("AI_QUICK",    "metallic"),
"make it glowing":       ("AI_QUICK",    "glowing"),
"make it wooden":        ("AI_QUICK",    "wooden"),
"make it stone":         ("AI_QUICK",    "stone"),
"make it glass":         ("AI_QUICK",    "glass"),

# voice to mesh
"create sphere":         ("VOICE_MESH",  "sphere"),
"create cube":           ("VOICE_MESH",  "cube"),
"create cylinder":       ("VOICE_MESH",  "cylinder"),
"create cone":           ("VOICE_MESH",  "cone"),
"create torus":          ("VOICE_MESH",  "torus"),
"create plane":          ("VOICE_MESH",  "plane"),
"create monkey":         ("VOICE_MESH",  "monkey"),
"generate mesh":         ("VOICE_MESH",  "ai"),
"build mesh":            ("VOICE_MESH",  "ai"),

# pose + dual hand
"enable pose":           ("POSE_TRACK",  True),
"disable pose":          ("POSE_TRACK",  False),
"dual hand mode":        ("DUAL_HAND",   True),
"single hand mode":      ("DUAL_HAND",   False),

# AR
"enable ar":             ("AR_MODE",     True),
"disable ar":            ("AR_MODE",     False),
"toggle ar":             ("AR_MODE",     "toggle"),
"ar mode":               ("AR_MODE",     "toggle"),
    # ── Phase 2 voice commands ───────────────────────────────────────
"viewport mode":       ("VIEWPORT_MODE", True),
"object mode":         ("VIEWPORT_MODE", False),
"toggle viewport":     ("VIEWPORT_MODE", "toggle"),
"play animation":      ("TIMELINE", "play"),
"stop animation":      ("TIMELINE", "stop"),
"pause animation":     ("TIMELINE", "stop"),
"rewind":              ("TIMELINE", "rewind"),
"next frame":          ("TIMELINE", "next"),
"previous frame":      ("TIMELINE", "prev"),
"go to start":         ("TIMELINE", "start"),
"go to end":           ("TIMELINE", "end"),
"enable face tracking":  ("FACE_TRACK", True),
"disable face tracking": ("FACE_TRACK", False),
"toggle face tracking":  ("FACE_TRACK", "toggle"),

    "snap":            ("SNAP", None),
    "snap to grid":    ("SNAP", None),
    "align":           ("SNAP", None),

    "array two":       ("ARRAY", 2),
    "array three":     ("ARRAY", 3),
    "array four":      ("ARRAY", 4),
    "array five":      ("ARRAY", 5),
    "array ten":       ("ARRAY", 10),

    "mirror x":        ("MIRROR", "X"),
    "mirror y":        ("MIRROR", "Y"),
    "mirror z":        ("MIRROR", "Z"),

    "screenshot":      ("SCREENSHOT", None),
    "take screenshot": ("SCREENSHOT", None),
    "capture":         ("SCREENSHOT", None),

    "undo":            ("UNDO", None),
    "redo":            ("REDO", None),
    # menu
    "menu":            ("MENU_TOGGLE", None),
    "help":            ("MENU_TOGGLE", None),
    "show menu":       ("MENU_TOGGLE", None),
    "next page":       ("MENU_NEXT",   None),
    "previous page":   ("MENU_PREV",   None),
    "close menu":      ("MENU_CLOSE",  None),
    # movement
    "move left":       ("GESTURE", "left"),
    "move right":      ("GESTURE", "right"),
    "move up":         ("GESTURE", "up"),
    "move down":       ("GESTURE", "down"),
    "move forward":    ("GESTURE", "forward"),
    "move back":       ("GESTURE", "back"),

    # scale
    "scale up":        ("SCALE",  1.2),
    "scale down":      ("SCALE",  0.8),
    "make bigger":     ("SCALE",  1.5),
    "make smaller":    ("SCALE",  0.5),
    "normalize":       ("JARVIS_ACTION", ("SCALE_NORMALIZE", {}, None)),

    # color
    "red":             ("COLOR", "red"),
    "green":           ("COLOR", "green"),
    "blue":            ("COLOR", "blue"),
    "yellow":          ("COLOR", "yellow"),
    "white":           ("COLOR", "white"),
    "black":           ("COLOR", "black"),

    # mesh operations
    "analyze":         ("JARVIS_ACTION", ("ANALYZE_MODEL", {}, None)),
    "analyse":         ("JARVIS_ACTION", ("ANALYZE_MODEL", {}, None)),
    "auto fix":        ("JARVIS_ACTION", ("AUTO_FIX",      {}, None)),
    "fix model":       ("JARVIS_ACTION", ("AUTO_FIX",      {}, None)),
    "smooth":          ("JARVIS_ACTION", ("SMOOTH_MODEL",  {}, None)),
    "decimate":        ("JARVIS_ACTION", ("DECIMATE_MODEL",{"ratio":0.5}, None)),
    "auto uv":         ("JARVIS_ACTION", ("AUTO_UV",       {}, None)),
    "wireframe":       ("JARVIS_ACTION", ("WIREFRAME_TOGGLE",{}, None)),

    # lighting
    "cinematic light": ("JARVIS_ACTION", ("SETUP_LIGHTING",{"style":"cinematic"}, None)),
    "sci fi light":    ("JARVIS_ACTION", ("SETUP_LIGHTING",{"style":"sci-fi"},    None)),
    "studio light":    ("JARVIS_ACTION", ("SETUP_LIGHTING",{"style":"studio"},    None)),
    "dramatic light":  ("JARVIS_ACTION", ("SETUP_LIGHTING",{"style":"dramatic"},  None)),

    # render
    "use cycles":      ("JARVIS_ACTION", ("SET_RENDER_ENGINE",{"engine":"CYCLES"}, None)),
    "use eevee":       ("JARVIS_ACTION", ("SET_RENDER_ENGINE",{"engine":"EEVEE"},  None)),

    # export
    "export glb":      ("JARVIS_ACTION", ("EXPORT_MODEL", {"format":"glb"}, None)),
    "export fbx":      ("JARVIS_ACTION", ("EXPORT_MODEL", {"format":"fbx"}, None)),
    "export obj":      ("JARVIS_ACTION", ("EXPORT_MODEL", {"format":"obj"}, None)),

    # system
    "delete":          ("DELETE", None),
    "delete object":   ("DELETE", None),
    "exit":            ("QUIT",   None),
    "quit":            ("QUIT",   None),
    "shutdown":        ("QUIT",   None),
}

def _match_command(text: str):
    """Find the best matching command for the spoken text."""
    text = text.lower().strip()

    # Exact or substring match — longest match wins
    best_key = None
    for key in LOCAL_COMMANDS:
        if key in text:
            if best_key is None or len(key) > len(best_key):
                best_key = key
    if best_key:
        return best_key, LOCAL_COMMANDS[best_key]

    # "import <name>" — dynamic
    if text.startswith("import "):
        name = text.replace("import", "").strip()
        return "import", ("IMPORT", name)

    # "select <name>"
    if text.startswith("select "):
        name = text.replace("select", "").strip()
        return "select", ("SELECT", name)

    return None, None


def _voice_loop():
    """Google SR — accurate, commands run 100% locally."""
    try:
        import speech_recognition as sr
    except ImportError:
        print("[VOICE] Install SpeechRecognition: pip install SpeechRecognition pyaudio")
        speak("Voice unavailable.")
        return

    rec = sr.Recognizer()
    rec.energy_threshold        = 300   # ignore very quiet background noise
    rec.dynamic_energy_threshold = True
    rec.pause_threshold          = 0.6  # faster response after speaking stops

    speak("Voice system ready.")
    print("[VOICE] Listening...")

    while running:
        try:
            with sr.Microphone() as src:
                rec.adjust_for_ambient_noise(src, duration=0.3)
                try:
                    audio = rec.listen(src, phrase_time_limit=4, timeout=5)
                    # Feed waveform data for HUD
                    raw = np.frombuffer(audio.get_raw_data(), dtype=np.int16)
                    chunk_size = max(1, len(raw) // 48)
                    with _waveform_lock:
                        for i in range(48):
                            idx = i * chunk_size
                            val = float(np.abs(raw[idx:idx+chunk_size]).mean()) / 32768.0
                            _waveform_samples.append(min(1.0, val * 4))
                except sr.WaitTimeoutError:
                    continue   # silence — keep looping, never freeze

            try:
                text = rec.recognize_google(audio).lower().strip()
            except sr.UnknownValueError:
                continue       # couldn't understand — keep looping
            except sr.RequestError as e:
                print(f"[VOICE] Google SR error: {e}")
                hud_message("Mic error — retrying", duration=2.0)
                time.sleep(1)
                continue

            # Skip noise / very short utterances
            noise_words = {"the","a","an","um","uh","oh","ah","and","or","so","is"}
            if len(text) < 3 or text in noise_words:
                print(f"[NOISE] {text}")
                continue

            print(f"[USER] {text}")

            key, cmd = _match_command(text)

            if cmd is None:
                print(f"[IGNORED] {text}")
                hud_message(f"? {text}", duration=2.0)
                speak("Command not recognised")
                continue

            cmd_type, cmd_data = cmd

            if cmd_type == "JARVIS_ACTION":
                action, params, _ = cmd_data
                obj = bpy.context.active_object
                command_queue.put(("JARVIS_ACTION", (action, params, obj)))
                speak(f"Running {action.lower().replace('_', ' ')}")
            elif cmd_type == "SCALE":
                command_queue.put(("SCALE_LOCAL", cmd_data))
            else:
                command_queue.put((cmd_type, cmd_data))

        except Exception as e:
            if running:
                print(f"[VOICE ERROR] {e}")
            time.sleep(0.5)    # brief pause then recover — never freeze
            
threading.Thread(target=_voice_loop, daemon=True).start()


def _apply_material(obj, preset: str, params: dict = None):
    """
    STARK Ultimate Material Engine
    Works for:
    • PLY meshes (vertex color lock fix)
    • Standard meshes
    • Blender 3.x / 4.x / 5.x
    • Viewport auto-switch
    """

    if not obj or obj.type != 'MESH':
        speak("No mesh selected.")
        return

    if params is None:
        params = {}

    # ─────────────────────────────────────────────
    # MATERIAL PRESETS (color, metallic, roughness)
    # ─────────────────────────────────────────────
    presets = {
        "red":        ((1.0,  0.02, 0.02, 1), 0.0, 0.5),
        "green":      ((0.05, 0.8,  0.05, 1), 0.0, 0.5),
        "blue":       ((0.02, 0.2,  1.0,  1), 0.0, 0.5),
        "yellow":     ((1.0,  0.9,  0.0,  1), 0.0, 0.5),
        "white":      ((0.95, 0.95, 0.95, 1), 0.0, 0.4),
        "black":      ((0.02, 0.02, 0.02, 1), 0.0, 0.6),
        "orange":     ((1.0,  0.35, 0.0,  1), 0.0, 0.5),
        "purple":     ((0.45, 0.0,  0.9,  1), 0.0, 0.5),
        "pink":       ((1.0,  0.2,  0.55, 1), 0.0, 0.5),
        "cyan":       ((0.0,  0.9,  1.0,  1), 0.0, 0.5),
        "gold":       ((1.0,  0.76, 0.15, 1), 1.0, 0.1),
        "copper":     ((0.72, 0.35, 0.15, 1), 1.0, 0.25),
        "futuristic": ((0.0,  0.85, 1.0,  1), 0.9, 0.05),
        "realistic":  ((0.6,  0.55, 0.5,  1), 0.0, 0.7),
        "cartoon":    ((1.0,  0.4,  0.05, 1), 0.0, 1.0),
        "metallic":   ((0.8,  0.8,  0.8,  1), 1.0, 0.02),
        "wooden":     ((0.35, 0.18, 0.06, 1), 0.0, 0.85),
        "stone":      ((0.4,  0.38, 0.35, 1), 0.0, 0.95),
        "rubber":     ((0.05, 0.05, 0.05, 1), 0.0, 1.0),
        "plastic":    ((0.9,  0.1,  0.1,  1), 0.0, 0.2),
        "glowing":    ((0.0,  1.0,  0.3,  1), 0.0, 0.5),
        "glass":      ((0.9,  0.95, 1.0,  1), 0.0, 0.0),
    }

    color, metallic, rough = presets.get(preset.lower(), ((1,1,1,1), 0.0, 0.5))

    # ─────────────────────────────────────────────
    # CREATE / GET MATERIAL
    # ─────────────────────────────────────────────
    mat_name = f"STARK_{preset.upper()}"
    mat = bpy.data.materials.get(mat_name) or bpy.data.materials.new(mat_name)
    try:
        mat.use_nodes = True
    except Exception:
        pass

    # Reset blend mode (important when switching from glass → solid)
    mat.blend_method = 'OPAQUE'
    #mat.shadow_method = 'OPAQUE'

    # ─────────────────────────────────────────────
    # 🚨 CRITICAL: CLEAR ALL NODES (PLY FIX)
    # ─────────────────────────────────────────────
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    # ─────────────────────────────────────────────
    # BUILD CLEAN NODE TREE
    # ─────────────────────────────────────────────
    bsdf   = nodes.new("ShaderNodeBsdfPrincipled")
    output = nodes.new("ShaderNodeOutputMaterial")
    links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

    # ─────────────────────────────────────────────
    # APPLY BASE PROPERTIES
    # ─────────────────────────────────────────────
    bsdf.inputs["Base Color"].default_value = color
    bsdf.inputs["Metallic"].default_value  = metallic
    bsdf.inputs["Roughness"].default_value = rough

    # ─────────────────────────────────────────────
    # SPECIAL MATERIAL TYPES
    # ─────────────────────────────────────────────

    # ✨ GLASS
    if preset == "glass":
        if "Transmission Weight" in bsdf.inputs:
            bsdf.inputs["Transmission Weight"].default_value = 1.0
        if "IOR" in bsdf.inputs:
            bsdf.inputs["IOR"].default_value = 1.45
        mat.blend_method  = 'BLEND'
        #mat.shadow_method = 'NONE'

    # ✨ GLOW / EMISSION
    if preset == "glowing":
        if "Emission Color" in bsdf.inputs:
            bsdf.inputs["Emission Color"].default_value = color
        if "Emission Strength" in bsdf.inputs:
            bsdf.inputs["Emission Strength"].default_value = 5.0

    # ─────────────────────────────────────────────
    # ASSIGN MATERIAL TO OBJECT
    # ─────────────────────────────────────────────
    if obj.data.materials:
        obj.data.materials[0] = mat
    else:
        obj.data.materials.append(mat)

    # ─────────────────────────────────────────────
    # FORCE VIEWPORT → MATERIAL MODE
    # ─────────────────────────────────────────────
    try:
        for area in bpy.context.screen.areas:
            if area.type == 'VIEW_3D':
                for space in area.spaces:
                    if space.type == 'VIEW_3D':
                        space.shading.type = 'MATERIAL'
                area.tag_redraw()
    except:
        pass

    speak(f"{preset} applied.")
    hud_message(f"◈ MATERIAL → {preset.upper()}", duration=3.0)
# ═══════════════════════════════════════════════════════════════════════════════
#  QUANTUM HUD OVERLAY
# ═══════════════════════════════════════════════════════════════════════════════
_hud_frame  = 0
_particles  = []

def _reset_particles(w=640, h=480, count=60):
    return [
        {
            "x":    random.randint(0, w),
            "y":    random.randint(0, h),
            "vx":   random.uniform(-1, 1),
            "vy":   random.uniform(-0.5, -2),
            "life": random.randint(20, 80),
        }
        for _ in range(count)
    ]

_particles = _reset_particles()

def _hex_grid(frame):
    h, w = frame.shape[:2]
    step = 36
    for row in range(0, h + step, step):
        offset = (step // 2) if (row // step) % 2 else 0
        for col_x in range(-step + offset, w + step, step):
            pts = [
                (int(col_x + 14 * math.cos(math.pi / 3 * i - math.pi / 6)),
                 int(row   + 14 * math.sin(math.pi / 3 * i - math.pi / 6)))
                for i in range(6)
            ]
            for i in range(6):
                cv2.line(frame, pts[i], pts[(i+1) % 6], (0, 60, 40), 1)

def _draw_arc(frame, cx, cy, r, start, sweep, color, thick=1):
    cv2.ellipse(frame, (cx, cy), (r, r), 0, start, start + sweep, color, thick)

def _neural_web(frame, t):
    h, w = frame.shape[:2]
    nodes = [
        (int(w * 0.15 + 80 * math.cos(t * 0.7 + i * 1.3)),
         int(h * 0.5  + 80 * math.sin(t * 0.5 + i * 1.1)))
        for i in range(6)
    ]
    for i, a in enumerate(nodes):
        for j, b in enumerate(nodes):
            if i < j:
                dist = math.hypot(a[0]-b[0], a[1]-b[1])
                if dist < 130:
                    alpha = int(255 * (1 - dist / 130))
                    cv2.line(frame, a, b, (0, alpha, int(alpha * 0.7)), 1)
    for n in nodes:
        cv2.circle(frame, n, 3, (0, 255, 180), -1)
        cv2.circle(frame, n, 6, (0, 180, 120),  1)

def _data_stream(frame, x, y, h_size, t):
    chars = "01ΞΛΨΩ∇∂∞⟨⟩◈▣⬡"
    C = (0, 255, 180)
    for i in range(h_size // 14):
        ch  = chars[int(t * 3 + i * 7) % len(chars)]
        br  = int(255 * abs(math.sin(t + i * 0.4)))
        cv2.putText(frame, ch, (x, y + i * 14),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.32, (0, br, int(br * 0.7)), 1)

def _glitch_line(frame):
    h, w = frame.shape[:2]
    if random.random() < 0.08:
        y      = random.randint(0, h - 4)
        offset = random.randint(-12, 12)
        frame[y:y+3, :] = np.roll(frame[y:y+3, :], offset, axis=1)

def _update_particles(frame, particles):
    h, w = frame.shape[:2]
    for p in particles:
        p["x"] += p["vx"]
        p["y"] += p["vy"]
        p["life"] -= 1
        if p["life"] <= 0 or p["y"] < 0:
            p["x"]    = random.randint(0, w)
            p["y"]    = h
            p["vx"]   = random.uniform(-0.8, 0.8)
            p["vy"]   = random.uniform(-1.5, -0.3)
            p["life"] = random.randint(40, 100)
        alpha = min(255, p["life"] * 4)
        cv2.circle(frame, (int(p["x"]), int(p["y"])), 1,
                   (0, alpha, int(alpha * 0.6)), -1)

def _energy_bar(frame, x, y, w_size, val, label, color):
    cv2.rectangle(frame, (x, y), (x + w_size, y + 8), (0, 30, 20), -1)
    cv2.rectangle(frame, (x, y), (x + int(w_size * val), y + 8), color, -1)
    cv2.rectangle(frame, (x, y), (x + w_size, y + 8), (0, 180, 120), 1)
    cv2.putText(frame, label, (x, y - 4),
                cv2.FONT_HERSHEY_SIMPLEX, 0.3, (0, 200, 140), 1)

def _warp_grid(frame, t):
    h, w = frame.shape[:2]
    for i in range(0, w, 48):
        off = int(6 * math.sin(t * 1.2 + i * 0.05))
        cv2.line(frame, (i, 0), (i + off, h), (0, 30, 20), 1)
    for j in range(0, h, 48):
        off = int(6 * math.cos(t * 1.0 + j * 0.05))
        cv2.line(frame, (0, j), (w, j + off), (0, 30, 20), 1)
def _draw_hud_messages(frame, t):
    """Render queued messages as animated sci-fi text on the HUD."""
    h, w = frame.shape[:2]
    now  = time.time()

    with _hud_msg_lock:
        # Remove expired messages
        active_msgs = [m for m in _hud_messages if now - m["born"] < m["duration"]]
        _hud_messages[:] = active_msgs

    if not active_msgs:
        return

    # Panel background — bottom-left area
    panel_x  = 28
    panel_y  = h - 55 - (len(active_msgs) * 28)
    panel_w  = w - 56
    panel_h  = len(active_msgs) * 28 + 14

    # Semi-transparent dark background
    overlay = frame.copy()
    cv2.rectangle(overlay,
                  (panel_x, panel_y),
                  (panel_x + panel_w, panel_y + panel_h),
                  (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.6, frame, 0.4, 0, frame)

    # Glowing border
    cv2.rectangle(frame,
                  (panel_x, panel_y),
                  (panel_x + panel_w, panel_y + panel_h),
                  (0, 255, 180), 1)

    # Left accent bar
    cv2.rectangle(frame,
                  (panel_x, panel_y),
                  (panel_x + 3, panel_y + panel_h),
                  (0, 255, 180), -1)

    # Label
    cv2.putText(frame, "◈ SYSTEM RESPONSE",
                (panel_x + 10, panel_y - 6),
                cv2.FONT_HERSHEY_SIMPLEX, 0.32, (0, 180, 120), 1)

    for i, msg in enumerate(active_msgs):
        age      = now - msg["born"]
        progress = age / msg["duration"]

        # Fade out in last 25%
        if progress > 0.75:
            alpha = int(255 * (1.0 - progress) * 4)
        else:
            alpha = 255

        # Typewriter effect — reveal characters over first 0.5s
        reveal = min(1.0, age / 0.5)
        chars_shown = max(1, int(len(msg["text"]) * reveal))
        display_text = msg["text"][:chars_shown]

        # Blinking cursor while typing
        if reveal < 1.0 and int(t * 15) % 2 == 0:
            display_text += "█"

        # Colour: newest = bright cyan, older = dimmer
        brightness = max(80, alpha)
        color = (0, brightness, int(brightness * 0.7))

        # Scanline shimmer on the active message
        if i == len(active_msgs) - 1:
            shimmer = int(40 * math.sin(t * 8 + i))
            color   = (0, min(255, brightness + shimmer),
                       int(min(255, brightness * 0.7 + shimmer * 0.5)))

        y_pos = panel_y + 18 + i * 28
        cv2.putText(frame, f"▶  {display_text}",
                    (panel_x + 12, y_pos),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, color, 1)

        # Progress bar under each message showing time remaining
        bar_w = int(panel_w * (1.0 - progress))
        cv2.rectangle(frame,
                      (panel_x + 6, y_pos + 5),
                      (panel_x + 6 + bar_w, y_pos + 7),
                      (0, 100, 70), -1)
def _draw_help_menu(frame, t):
    """Animated full-screen sci-fi help menu overlay."""
    if not _menu_visible:
        return

    h, w   = frame.shape[:2]
    age    = time.time() - _menu_anim_start
    page   = MENU_PAGES[_menu_page]
    C      = page["color"]
    WHITE  = (220, 220, 220)
    DIM    = (80, 80, 80)

    # ── Entry slide-in animation (first 0.3s) ────────────────────
    slide = min(1.0, age / 0.3)
    # Ease out cubic
    slide = 1 - (1 - slide) ** 3

    # Panel dimensions
    pw    = int(w * 0.72 * slide)
    ph    = int(h * 0.80)
    px    = (w - pw) // 2
    py    = (h - ph) // 2

    if pw < 20:
        return

    # ── Dark overlay behind panel ────────────────────────────────
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (w, h), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.65, frame, 0.35, 0, frame)

    # ── Main panel background ────────────────────────────────────
    cv2.rectangle(frame, (px, py), (px + pw, py + ph), (0, 8, 5), -1)

    # ── Animated scan line sweeping down the panel ───────────────
    scan_y = py + int((ph) * ((t * 0.4) % 1.0))
    cv2.line(frame, (px, scan_y), (px + pw, scan_y), (0, 40, 25), 1)

    # ── Corner brackets ─────────────────────────────────────────
    arm = 22
    for (ex, ey), (dx, dy) in [
        ((px, py), (1, 1)), ((px+pw, py), (-1, 1)),
        ((px, py+ph), (1, -1)), ((px+pw, py+ph), (-1, -1))
    ]:
        cv2.line(frame, (ex, ey), (ex + dx*arm, ey), C, 2)
        cv2.line(frame, (ex, ey), (ex, ey + dy*arm), C, 2)

    # ── Outer border ────────────────────────────────────────────
    cv2.rectangle(frame, (px, py), (px + pw, py + ph), C, 1)

    # ── Header bar ──────────────────────────────────────────────
    cv2.rectangle(frame, (px, py), (px + pw, py + 36), (0, 15, 10), -1)
    cv2.line(frame, (px, py + 36), (px + pw, py + 36), C, 1)

    # Pulsing dot in header
    pulse = int(4 + 2 * math.sin(t * 5))
    cv2.circle(frame, (px + 18, py + 18), pulse, C, -1)

    cv2.putText(frame, page["title"],
                (px + 32, py + 24),
                cv2.FONT_HERSHEY_SIMPLEX, 0.52, C, 1)

    # Page indicator right side of header
    page_txt = f"PAGE  {_menu_page + 1} / {len(MENU_PAGES)}"
    cv2.putText(frame, page_txt,
                (px + pw - 130, py + 24),
                cv2.FONT_HERSHEY_SIMPLEX, 0.38, C, 1)

    # ── Column headers ───────────────────────────────────────────
    col_y = py + 54
    cv2.putText(frame, "VOICE COMMAND",
                (px + 20, col_y),
                cv2.FONT_HERSHEY_SIMPLEX, 0.36, C, 1)
    cv2.putText(frame, "ACTION",
                (px + pw//2 + 10, col_y),
                cv2.FONT_HERSHEY_SIMPLEX, 0.36, C, 1)
    cv2.line(frame, (px + 10, col_y + 6),
             (px + pw - 10, col_y + 6), (0, 60, 40), 1)

    # ── Items with staggered fade-in ────────────────────────────
    row_h  = (ph - 90) // max(len(page["items"]), 1)
    row_h  = min(row_h, 42)

    for i, (cmd_txt, desc_txt) in enumerate(page["items"]):
        # Stagger: each row fades in 0.06s after the previous
        item_age   = age - i * 0.06
        item_alpha = max(0.0, min(1.0, item_age / 0.15))
        brightness = int(200 * item_alpha)

        if brightness <= 0:
            continue

        iy     = col_y + 18 + i * row_h
        row_c  = (0, brightness, int(brightness * 0.65))
        desc_c = (int(brightness * 0.75), int(brightness * 0.75),
                  int(brightness * 0.75))

        # Highlight row the scan line is near
        if abs(scan_y - iy) < row_h:
            highlight = frame.copy()
            cv2.rectangle(highlight,
                          (px + 4, iy - 14),
                          (px + pw - 4, iy + 8),
                          (0, 30, 20), -1)
            cv2.addWeighted(highlight, 0.5, frame, 0.5, 0, frame)

        # Command bullet
        cv2.putText(frame, f"▸  {cmd_txt}",
                    (px + 20, iy),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.40, row_c, 1)

        # Divider dots
        for dot_x in range(px + pw//2 - 20, px + pw//2 + 20, 6):
            cv2.circle(frame, (dot_x, iy - 4), 1, (0, 50, 35), -1)

        # Description
        cv2.putText(frame, desc_txt,
                    (px + pw//2 + 10, iy),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, desc_c, 1)

        # Separator line
        cv2.line(frame,
                 (px + 10, iy + 10),
                 (px + pw - 10, iy + 10),
                 (0, 25, 16), 1)

    # ── Footer navigation hint ───────────────────────────────────
    footer_y = py + ph - 14
    cv2.line(frame,
             (px, footer_y - 18),
             (px + pw, footer_y - 18), C, 1)

    hints = [
        (f"◀  SAY 'PREVIOUS PAGE'", px + 16),
        (f"SAY 'CLOSE MENU'  ✕",    px + pw//2 - 60),
        (f"SAY 'NEXT PAGE'  ▶",     px + pw - 180),
    ]
    for txt, hx in hints:
        # Blinking
        if int(t * 3) % 2 == 0:
            cv2.putText(frame, txt, (hx, footer_y),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.32, (0, 130, 90), 1)

    # ── Side page dots indicator ─────────────────────────────────
    dot_x = px + pw + 12
    for i in range(len(MENU_PAGES)):
        dot_y   = py + ph//2 + (i - len(MENU_PAGES)//2) * 16
        is_cur  = (i == _menu_page)
        radius  = int(5 + 2 * math.sin(t * 4)) if is_cur else 3
        color   = C if is_cur else (0, 60, 40)
        cv2.circle(frame, (dot_x, dot_y), radius, color, -1 if is_cur else 1) 
def _draw_scene_objects(frame, t):
    """Show all scene objects on HUD — bottom left panel."""
    h, w = frame.shape[:2]
    objects = [o for o in bpy.data.objects if o.type == 'MESH']
    if not objects:
        return

    panel_x, panel_y = 28, h - 60 - len(objects) * 22 - 30
    panel_w = 180
    panel_h = len(objects) * 22 + 30

    # Background
    overlay = frame.copy()
    cv2.rectangle(overlay, (panel_x, panel_y),
                  (panel_x+panel_w, panel_y+panel_h), (0,0,0), -1)
    cv2.addWeighted(overlay, 0.55, frame, 0.45, 0, frame)
    cv2.rectangle(frame, (panel_x, panel_y),
                  (panel_x+panel_w, panel_y+panel_h), (0,180,255), 1)

    cv2.putText(frame, "◈ SCENE OBJECTS",
                (panel_x+6, panel_y+14),
                cv2.FONT_HERSHEY_SIMPLEX, 0.32, (0,180,255), 1)
    cv2.line(frame, (panel_x, panel_y+18),
             (panel_x+panel_w, panel_y+18), (0,60,80), 1)

    active_obj = bpy.context.active_object
    for i, o in enumerate(objects[:6]):  # max 6 shown
        iy      = panel_y + 30 + i * 22
        is_active = (o == active_obj)
        col     = (0, 255, 180) if is_active else (0, 130, 100)
        prefix  = "▶ " if is_active else "  "
        # Pulse active object
        if is_active:
            pulse = int(3 + 2 * math.sin(t * 5))
            cv2.circle(frame, (panel_x + 10, iy - 4), pulse, (0,255,180), -1)
        cv2.putText(frame, f"{prefix}{o.name[:16]}",
                    (panel_x+20, iy),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.33, col, 1)        
def _draw_voice_waveform(frame, t):
    """Animated voice waveform — top centre of HUD."""
    h, w = frame.shape[:2]
    cx   = w // 2
    cy   = 24          # inside top bar
    bars = 48
    bar_w = 4
    total = bars * (bar_w + 1)
    start_x = cx - total // 2

    with _waveform_lock:
        samples = list(_waveform_samples)

    # Pad if not enough data yet
    while len(samples) < bars:
        samples.append(0.02)

    for i, amp in enumerate(samples[-bars:]):
        # Idle animation when no voice
        if amp < 0.03:
            amp = 0.03 + 0.02 * math.sin(t * 3 + i * 0.3)

        bar_h  = int(amp * 28)
        bx     = start_x + i * (bar_w + 1)
        # Colour: green → cyan based on amplitude
        g = min(255, int(amp * 600))
        b = min(255, int(amp * 300))
        cv2.rectangle(frame,
                      (bx, cy - bar_h),
                      (bx + bar_w, cy + bar_h),
                      (0, g, b), -1)     
# ═══════════════════════════════════════════════════════════════════════════════
#  PHASE 3 — CLAUDE AI ENGINE
# ═══════════════════════════════════════════════════════════════════════════════
def _ai_call_async(prompt: str, on_done):
    """
    Run a Claude API call in a background thread.
    on_done(response_text) is called when complete —
    result is pushed to _ai_response_queue for main thread pickup.
    """
    global _ai_busy

    if _ai_busy:
        hud_message("◈ AI BUSY — please wait", duration=2.0)
        return

    if _ai_client is None:
        hud_message("◈ AI NOT CONNECTED", duration=3.0)
        speak("AI not connected. Check API key.")
        return

    _ai_busy = True
    hud_message("◈ AI THINKING...", duration=8.0)

    def _worker():
        global _ai_busy
        try:
            msg = _ai_client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}]
            )
            result = msg.content[0].text
            _ai_response_queue.put(("OK", result, on_done))
        except Exception as e:
            _ai_response_queue.put(("ERROR", str(e), None))
        finally:
            _ai_busy = False

    threading.Thread(target=_worker, daemon=True).start()


def _build_material_prompt(obj, style: str) -> str:
    """Build a prompt asking Claude for a Blender material."""
    name    = obj.name if obj else "unknown object"
    dims    = str(tuple(round(d, 2) for d in obj.dimensions)) if obj else "unknown"
    cached  = analysis_cache.get(obj.name, {}) if obj else {}
    score   = cached.get("score", "not analysed")

    return f"""You are a Blender Python expert. Generate ONLY executable Blender Python code
to create a '{style}' material for an object named '{name}' 
with dimensions {dims} and mesh quality score {score}.

Rules:
- Use Principled BSDF node
- Set realistic values for metallic, roughness, base color
- Add emission if glowing style
- Use node_tree for advanced effects
- Output ONLY raw Python code, no markdown, no explanation
- Assume 'obj' variable already holds the active object
- Create material named 'AI_{style}'

Example structure:
mat = bpy.data.materials.new('AI_{style}')
mat.use_nodes = True
nodes = mat.node_tree.nodes
bsdf = nodes['Principled BSDF']
bsdf.inputs['Base Color'].default_value = (R, G, B, 1)
bsdf.inputs['Metallic'].default_value = X
bsdf.inputs['Roughness'].default_value = X
if not obj.data.materials:
    obj.data.materials.append(mat)
else:
    obj.data.materials[0] = mat"""


def _build_mesh_prompt(description: str) -> str:
    """Build a prompt asking Claude to generate a mesh via Python."""
    return f"""You are a Blender Python expert. Generate ONLY executable Blender Python code
to create a 3D mesh that looks like: '{description}'

Rules:
- Use bmesh to build geometry from scratch
- Place result at world origin
- Keep polygon count under 2000
- Output ONLY raw Python code, no markdown, no explanation
- End by linking mesh to scene and making it active

Example structure:
import bpy, bmesh, math
mesh = bpy.data.meshes.new('{description}')
obj  = bpy.data.objects.new('{description}', mesh)
bpy.context.collection.objects.link(obj)
bpy.context.view_layer.objects.active = obj
bm = bmesh.new()
# ... build geometry ...
bm.to_mesh(mesh)
bm.free()"""


def _execute_ai_code(code: str):
    """Safely execute AI-generated Blender Python on the main thread."""
    try:
        # Basic safety check — block file ops
        blocked = ["open(", "os.remove", "shutil", "subprocess",
                   "import os", "__import__"]
        for b in blocked:
            if b in code:
                speak("AI code blocked for safety")
                hud_message(f"◈ BLOCKED: {b}", duration=4.0)
                return False

        exec(compile(code, "<ai_generated>", "exec"),
             {"bpy": bpy, "bmesh": bmesh, "math": math,
              "np": np, "obj": bpy.context.active_object})
        return True
    except Exception as e:
        print(f"[AI EXEC] {e}")
        hud_message(f"◈ AI CODE ERROR: {str(e)[:30]}", duration=4.0)
        speak("AI code execution failed")
        return False


def _process_ai_queue():
    """
    Called every frame from control_loop — picks up AI results
    and executes them on the main thread.
    """
    try:
        status, data, callback = _ai_response_queue.get_nowait()
        if status == "OK":
            if callback:
                callback(data)
            hud_message("◈ AI COMPLETE", duration=3.0)
        else:
            hud_message(f"◈ AI ERROR: {data[:30]}", duration=4.0)
            speak("AI request failed")
    except Empty:
        pass
# ═══════════════════════════════════════════════════════════════════════════════
#  PHASE 3 — POSE DETECTION + DUAL HAND
# ═══════════════════════════════════════════════════════════════════════════════
def _process_pose(frame, rgb, obj):
    """
    Full body pose — left hand controls object X/Z,
    right hand controls scale + Y depth simultaneously.
    """
    global _left_hand_lm, _right_hand_lm

    if not _pose_tracking_on or _pose_detector is None:
        return

    try:
        result = _pose_detector.process(rgb)
        if not result.pose_landmarks:
            return

        lm = result.pose_landmarks.landmark

        # MediaPipe pose landmark indices
        L_WRIST = 15
        R_WRIST = 16
        L_SHOULDER = 11
        R_SHOULDER = 12

        lw = lm[L_WRIST]
        rw = lm[R_WRIST]

        if not obj:
            return

        # Left wrist → object X/Z position
        if lw.visibility > 0.6:
            tx = (lw.x - 0.5) * MOVE_SENS
            tz = (0.5 - lw.y) * MOVE_SENS
            obj.location.x = lerp(obj.location.x, tx, SMOOTH)
            obj.location.z = lerp(obj.location.z, tz, SMOOTH * 0.5)

        # Right wrist → object Y depth + scale
        if rw.visibility > 0.6:
            ty = (rw.x - 0.5) * MOVE_SENS
            obj.location.y = lerp(obj.location.y, ty, SMOOTH)

            # Right hand height → scale
            rh = 1.0 - rw.y   # 0=bottom 1=top
            target_scale = max(0.1, rh * 3.0)
            obj.scale = (
                lerp(obj.scale.x, target_scale, 0.05),
                lerp(obj.scale.y, target_scale, 0.05),
                lerp(obj.scale.z, target_scale, 0.05),
            )

        # Draw skeleton on HUD
        h, w = frame.shape[:2]
        joints = [L_WRIST, R_WRIST, L_SHOULDER, R_SHOULDER]
        pts = {}
        for idx in joints:
            pts[idx] = (int(lm[idx].x * w), int(lm[idx].y * h))

        # Draw arms
        cv2.line(frame, pts[L_SHOULDER], pts[L_WRIST], (0, 255, 180), 2)
        cv2.line(frame, pts[R_SHOULDER], pts[R_WRIST], (0, 180, 255), 2)

        for idx, pt in pts.items():
            cv2.circle(frame, pt, 5, (0, 255, 180), -1)
            cv2.circle(frame, pt, 8, (0, 180, 120), 1)

    except Exception as e:
        print(f"[POSE] {e}")


def _process_dual_hand(result, obj):
    """
    Two hands detected simultaneously:
    Left hand  → move object (X/Z)
    Right hand → rotate object (Y axis) + pinch to scale
    """
    if not _dual_hand_mode:
        return
    if not result.multi_hand_landmarks:
        return
    if len(result.multi_hand_landmarks) < 2:
        return
    if not obj:
        return

    try:
        # Determine which hand is left/right from handedness
        handedness = result.multi_handedness
        hands      = {}
        for i, h in enumerate(handedness):
            label = h.classification[0].label  # 'Left' or 'Right'
            hands[label] = result.multi_hand_landmarks[i].landmark

        lm_l = hands.get("Left")
        lm_r = hands.get("Right")

        if lm_l:
            # Left hand → position
            tx = (lm_l[9].x - 0.5) * MOVE_SENS
            tz = (0.5 - lm_l[9].y) * MOVE_SENS
            obj.location.x = lerp(obj.location.x, tx, SMOOTH)
            obj.location.z = lerp(obj.location.z, tz, SMOOTH)

        if lm_r:
            # Right hand → rotation
            obj.rotation_euler.y = lerp(
                obj.rotation_euler.y,
                (lm_r[0].x - lm_r[9].x) * math.pi * 2,
                SMOOTH)
            obj.rotation_euler.x = lerp(
                obj.rotation_euler.x,
                (lm_r[0].y - lm_r[9].y) * math.pi,
                SMOOTH)

            # Right hand pinch → scale
            pinch = math.dist(
                (lm_r[4].x, lm_r[4].y),
                (lm_r[8].x, lm_r[8].y))
            if pinch < 0.04:
                s = obj.scale.x + PINCH_SCALE_SPEED
                obj.scale = (s, s, s)

    except Exception as e:
        print(f"[DUAL HAND] {e}")
# ═══════════════════════════════════════════════════════════════════════════════
#  PHASE 3 — AR OVERLAY
# ═══════════════════════════════════════════════════════════════════════════════
def _draw_ar_overlay(frame, obj, t):
    """
    Project the active Blender object's bounding box
    onto the real camera feed as an AR overlay.
    Estimates ground plane from the bottom of the frame.
    """
    if not _ar_mode or obj is None:
        return

    h, w  = frame.shape[:2]
    C     = (0, 255, 180)

    import mathutils

    try:
        # Camera intrinsics — approximate for typical webcam
        fx = fy = w * 1.1
        cx, cy  = w / 2, h / 2

        # Object bounding box corners
        bx = obj.dimensions.x / 2
        by = obj.dimensions.y / 2
        bz = obj.dimensions.z / 2

        corners = [
            (-bx,-by, 0), ( bx,-by, 0),
            ( bx, by, 0), (-bx, by, 0),
            (-bx,-by, bz*2), ( bx,-by, bz*2),
            ( bx, by, bz*2), (-bx, by, bz*2),
        ]

        # Place AR object at bottom-centre of frame
        ar_dist = 1.5
        ar_x    = 0.0
        ar_y    = ar_dist
        ar_z    = -0.3

        projected = []
        for lx, ly, lz in corners:
            wx = lx + ar_x
            wy = ly + ar_y
            wz = lz + ar_z

            if wy < 0.01:
                projected.append(None)
                continue

            # Perspective projection
            sx = int(cx + fx * wx / wy)
            sz = int(cy - fy * wz / wy)
            projected.append((sx, sz))

        # Draw AR bounding box edges
        edges = [
            (0,1),(1,2),(2,3),(3,0),
            (4,5),(5,6),(6,7),(7,4),
            (0,4),(1,5),(2,6),(3,7),
        ]

        # Ground shadow (filled polygon)
        base = [projected[i] for i in range(4) if projected[i]]
        if len(base) == 4:
            pts = np.array(base, dtype=np.int32)
            overlay = frame.copy()
            cv2.fillPoly(overlay, [pts], (0, 40, 20))
            cv2.addWeighted(overlay, 0.3, frame, 0.7, 0, frame)

        pulse = int(180 + 60 * math.sin(t * 4))
        edge_col = (0, pulse, int(pulse * 0.7))

        for a, b in edges:
            if projected[a] and projected[b]:
                cv2.line(frame, projected[a], projected[b],
                         edge_col, 1)

        # AR label
        if projected[4]:
            cv2.putText(frame,
                        f"[ AR: {obj.name} ]",
                        (projected[4][0] + 6, projected[4][1] - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.36,
                        (0, 200, 140), 1)

        # AR mode indicator
        cv2.putText(frame, "◈ AR ACTIVE",
                    (w - 120, 28),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38,
                    (0, 255, 180) if int(t*4)%2==0 else (0,120,90), 1)

    except Exception as e:
        print(f"[AR OVERLAY] {e}")
def _draw_ai_status(frame, t):
    """AI status panel — top right area."""
    h, w = frame.shape[:2]
    C    = (0, 255, 180)

    px, py = w - 210, 340
    pw, ph = 196, 90

    overlay = frame.copy()
    cv2.rectangle(overlay, (px, py), (px+pw, py+ph), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.5, frame, 0.5, 0, frame)
    cv2.rectangle(frame, (px, py), (px+pw, py+ph), C, 1)
    cv2.line(frame, (px, py+18), (px+pw, py+18), (0, 60, 40), 1)

    cv2.putText(frame, "◈ AI STATUS",
                (px+6, py+13),
                cv2.FONT_HERSHEY_SIMPLEX, 0.32, C, 1)

    states = [
        ("CLAUDE",  "READY" if _ai_client else "OFFLINE",
         (0,200,100) if _ai_client else (0,60,200)),
        ("POSE",    "ON" if _pose_tracking_on else "OFF",
         (0,200,100) if _pose_tracking_on else (0,80,60)),
        ("DUAL",    "ON" if _dual_hand_mode else "OFF",
         (0,200,100) if _dual_hand_mode else (0,80,60)),
        ("AR",      "ON" if _ar_mode else "OFF",
         (0,200,100) if _ar_mode else (0,80,60)),
    ]

    for i, (label, val, col) in enumerate(states):
        cv2.putText(frame, f"{label:<8} {val}",
                    (px+8, py+32+i*16),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.30, col, 1)

    # Pulsing dot when AI is busy
    if _ai_busy:
        pulse = int(4 + 2*math.sin(t*10))
        cv2.circle(frame, (px+pw-10, py+10), pulse, (0, 255, 120), -1)
        cv2.putText(frame, "THINKING",
                    (px+pw-80, py+13),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.28, (0,200,100), 1)
# ═══════════════════════════════════════════════════════════════════════════════
#  PHASE 3 — AI QUICK MATERIAL PRESETS
# ═══════════════════════════════════════════════════════════════════════════════
AI_QUICK_PRESETS = {
    "futuristic": dict(
        base_color=(0.0, 0.8, 1.0, 1),
        metallic=0.95, roughness=0.05,
        emission=(0.0, 0.5, 1.0), emission_strength=2.0),
    "realistic": dict(
        base_color=(0.6, 0.5, 0.4, 1),
        metallic=0.0,  roughness=0.6,
        emission=None, emission_strength=0.0),
    "cartoon": dict(
        base_color=(1.0, 0.4, 0.1, 1),
        metallic=0.0,  roughness=1.0,
        emission=None, emission_strength=0.0),
    "metallic": dict(
        base_color=(0.8, 0.8, 0.9, 1),
        metallic=1.0,  roughness=0.1,
        emission=None, emission_strength=0.0),
    "glowing": dict(
        base_color=(0.0, 1.0, 0.5, 1),
        metallic=0.3,  roughness=0.2,
        emission=(0.0, 1.0, 0.5), emission_strength=5.0),
    "wooden": dict(
        base_color=(0.4, 0.25, 0.1, 1),
        metallic=0.0,  roughness=0.9,
        emission=None, emission_strength=0.0),
    "stone": dict(
        base_color=(0.4, 0.38, 0.35, 1),
        metallic=0.0,  roughness=0.95,
        emission=None, emission_strength=0.0),
    "glass": dict(
        base_color=(0.9, 0.95, 1.0, 1),
        metallic=0.0,  roughness=0.0,
        emission=None, emission_strength=0.0,
        transmission=1.0),
}

def _apply_quick_preset(style: str, obj):
    """Apply a quick material preset — no API call, instant."""
    if not obj:
        speak("No object selected")
        return

    p   = AI_QUICK_PRESETS.get(style, AI_QUICK_PRESETS["futuristic"])
    mat = bpy.data.materials.new(f"AI_{style}")
    mat.use_nodes = True

    if style == "glass":
        mat.blend_method    = 'BLEND'
        #mat.shadow_method   = 'NONE'

    nodes = mat.node_tree.nodes
    bsdf  = nodes.get("Principled BSDF")
    if not bsdf:
        return

    bsdf.inputs["Base Color"].default_value  = p["base_color"]
    bsdf.inputs["Metallic"].default_value    = p["metallic"]
    bsdf.inputs["Roughness"].default_value   = p["roughness"]

    if p.get("transmission"):
        bsdf.inputs["Transmission"].default_value = p["transmission"]

    if p.get("emission"):
        ec = p["emission"]
        bsdf.inputs["Emission"].default_value = (ec[0], ec[1], ec[2], 1)
        em = bsdf.inputs.get("Emission Strength")
        if em:
            em.default_value = p["emission_strength"]

    if not obj.data.materials:
        obj.data.materials.append(mat)
    else:
        obj.data.materials[0] = mat

    speak(f"{style} material applied")
    hud_message(f"◈ AI MATERIAL: {style.upper()}", duration=3.0)
# ═══════════════════════════════════════════════════════════════════════════════
#  BLENDER OFFSCREEN MODEL THUMBNAIL  —  pre-baked GPU buffers
# ═══════════════════════════════════════════════════════════════════════════════
import gpu
from gpu_extras.presets import draw_texture_2d

_thumb_size      = 400
_thumb_rotation  = 0.0
_offscreen       = None

# Baked mesh state — rebuilt only when active object changes
_baked_obj_name  = None
_baked_vbo       = None
_baked_tri_count = 0
_baked_shader    = None


def _get_offscreen():
    global _offscreen
    try:
        if _offscreen is None:
            _offscreen = gpu.types.GPUOffScreen(_thumb_size, _thumb_size)
    except Exception as e:
        print(f"[OFFSCREEN INIT] {e}")
        _offscreen = None
    return _offscreen


def _bake_mesh(obj):
    """
    Pre-compute vertex positions + per-vertex shaded colors into a GPUVertBuf.
    Called ONCE when the active object changes — never per-frame.
    """
    global _baked_obj_name, _baked_vbo, _baked_tri_count, _baked_shader

    if obj is None or obj.type != 'MESH':
        _baked_vbo       = None
        _baked_tri_count = 0
        _baked_obj_name  = None
        return

    try:
        mesh = obj.data
        mesh.calc_loop_triangles()

        # World-space vertex positions
        wm    = obj.matrix_world
        verts = [wm @ v.co for v in mesh.vertices]

        tris     = mesh.loop_triangles
        n_tris   = len(tris)
        if n_tris == 0:
            return

        positions = np.empty((n_tris * 3, 3), dtype=np.float32)
        normals   = np.empty((n_tris * 3, 3), dtype=np.float32)

        for i, lt in enumerate(tris):
            for j in range(3):
                positions[i*3+j] = verts[lt.vertices[j]]
            n = lt.normal
            for j in range(3):
                normals[i*3+j] = (n.x, n.y, n.z)

        # Normalise positions to a unit sphere centred at origin
        # so the camera distance is always consistent
        centre = positions.mean(axis=0)
        positions -= centre
        scale  = np.linalg.norm(positions, axis=1).max()
        if scale > 0:
            positions /= scale

        # Fixed key-light direction (top-left-front)
        light = np.array([0.6, -0.5, 0.8], dtype=np.float32)
        light /= np.linalg.norm(light)

        diffuse = np.clip(normals @ light, 0.0, 1.0)
        ambient = 0.18

        colors = np.zeros((n_tris * 3, 4), dtype=np.float32)
        colors[:, 0] = (diffuse * 0.05 + ambient)          # R  (keep low → cyan)
        colors[:, 1] = (diffuse * 0.90 + ambient)          # G
        colors[:, 2] = (diffuse * 0.75 + ambient)          # B
        colors[:, 3] = 1.0

        fmt = gpu.types.GPUVertFormat()
        fmt.attr_add(id="pos",   comp_type='F32', len=3, fetch_mode='FLOAT')
        fmt.attr_add(id="color", comp_type='F32', len=4, fetch_mode='FLOAT')

        vbo = gpu.types.GPUVertBuf(format=fmt, len=n_tris * 3)
        vbo.attr_fill(id="pos",   data=positions)
        vbo.attr_fill(id="color", data=colors)

        _baked_vbo       = gpu.types.GPUBatch(type='TRIS', buf=vbo)
        _baked_tri_count = n_tris
        _baked_obj_name  = obj.name
        _baked_shader    = gpu.shader.from_builtin('SMOOTH_COLOR')

        print(f"[THUMB] Baked {n_tris:,} triangles for '{obj.name}'")

    except Exception as e:
        print(f"[BAKE MESH] {e}")
        _baked_vbo = None


def _render_model_thumbnail(obj):
    """
    Fast per-frame call: just updates the rotation matrix and issues one draw.
    Mesh data comes from pre-baked VBO — zero numpy work per frame.
    """
    global _thumb_rotation, _baked_obj_name

    if obj is None or obj.type != 'MESH':
        return None

    # Re-bake only when object changes
    if obj.name != _baked_obj_name:
        _bake_mesh(obj)

    if _baked_vbo is None:
        return None

    ofs = _get_offscreen()
    if ofs is None:
        return None

    import mathutils

    try:
        _thumb_rotation += 0.018          # smooth auto-spin

        # Camera sits at fixed distance on unit sphere
        dist  = 2.4
        cam_x = dist * math.sin(_thumb_rotation)
        cam_y = -dist * math.cos(_thumb_rotation)
        cam_z = 0.7

        eye     = mathutils.Vector((cam_x, cam_y, cam_z))
        target  = mathutils.Vector((0, 0, 0))
        up      = mathutils.Vector((0, 0, 1))

        forward = (eye - target).normalized()
        right   = up.cross(forward).normalized()
        true_up = forward.cross(right).normalized()

        view = mathutils.Matrix((
            ( right.x,   right.y,   right.z,  -right.dot(eye)),
            ( true_up.x, true_up.y, true_up.z,-true_up.dot(eye)),
            ( forward.x, forward.y, forward.z,-forward.dot(eye)),
            ( 0,         0,         0,         1),
        ))

        fov  = math.radians(40)
        near, far = 0.1, 100.0
        f    = 1.0 / math.tan(fov / 2)
        proj = mathutils.Matrix((
            (f, 0, 0,                          0),
            (0, f, 0,                          0),
            (0, 0, (far+near)/(near-far),      (2*far*near)/(near-far)),
            (0, 0, -1,                         0),
        ))

        sz = _thumb_size

        with ofs.bind():
            fb = gpu.state.active_framebuffer_get()
            fb.clear(color=(0.03, 0.03, 0.07, 1.0))

            with gpu.matrix.push_pop():
                gpu.matrix.load_matrix(view)
                gpu.matrix.load_projection_matrix(proj)

                gpu.state.depth_test_set('LESS_EQUAL')
                gpu.state.face_culling_set('BACK')

                _baked_shader.bind()
                _baked_vbo.draw(_baked_shader)

                gpu.state.depth_test_set('NONE')
                gpu.state.face_culling_set('NONE')

        # Read pixels — this is the only unavoidable per-frame cost
        pixel_data = ofs.texture_color.read()
        pixel_data.dimensions = sz * sz * 4
        arr = np.frombuffer(pixel_data, dtype=np.uint8).reshape((sz, sz, 4))
        arr = np.flipud(arr)
        return cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)

    except Exception as e:
        print(f"[THUMB RENDER] {e}")
        return None


def _draw_model_thumbnail(frame, obj, t):
    """Overlay the rendered thumbnail onto the HUD camera frame."""
    thumb = _render_model_thumbnail(obj)
    if thumb is None:
        return

    h, w  = frame.shape[:2]
    sz    = _thumb_size
    C     = (0, 255, 180)
    C2    = (0, 180, 255)

    px = w - sz - 14
    py = h - sz - 120

    # Blend onto frame
    roi     = frame[py:py+sz, px:px+sz]
    blended = cv2.addWeighted(thumb, 0.92, roi, 0.08, 0)
    frame[py:py+sz, px:px+sz] = blended

    # Corner brackets — pulsing
    arm   = 14
    thick = max(1, int(1 + math.sin(t * 5)))
    for (ex, ey), (dx, dy) in [
        ((px,    py),    ( 1,  1)),
        ((px+sz, py),    (-1,  1)),
        ((px,    py+sz), ( 1, -1)),
        ((px+sz, py+sz), (-1, -1)),
    ]:
        cv2.line(frame, (ex, ey), (ex + dx*arm, ey),     C, thick)
        cv2.line(frame, (ex, ey), (ex, ey + dy*arm),     C, thick)

    # Border
    cv2.rectangle(frame, (px, py), (px+sz, py+sz), C2, 1)

    # Scan line sweep
    scan_y = py + int(sz * ((t * 0.4) % 1.0))
    cv2.line(frame, (px, scan_y), (px+sz, scan_y), (0, 45, 30), 1)

    # Object name label
    name = (obj.name[:16] if obj else "NO TARGET")
    cv2.putText(frame, f"◈ {name}",
                (px, py - 6),
                cv2.FONT_HERSHEY_SIMPLEX, 0.36, C2, 1)

    # Tri count badge
    cv2.putText(frame, f"{_baked_tri_count:,} tris",
                (px + sz - 58, py - 6),
                cv2.FONT_HERSHEY_SIMPLEX, 0.30, (0, 130, 90), 1)

    # Spinning ready dot
    cv2.circle(frame,
               (px + sz - 8, py + 8),
               int(3 + math.sin(t * 6)),
               C, -1)


def draw_scifi_hud(frame, obj, hand_detected):
    global _hud_frame, _particles
    _hud_frame += 1
    t   = _hud_frame * 0.04
    h, w = frame.shape[:2]
    C   = (0, 255, 180)
    C2  = (0, 180, 255)
    C3  = (180, 0, 255)
    DIM = (0, 80, 55)

    _warp_grid(frame, t)
    _hex_grid(frame)
    _update_particles(frame, _particles)
    _glitch_line(frame)
    _neural_web(frame, t)
    _data_stream(frame, 6,    50, h - 100, t)
    _data_stream(frame, w-18, 50, h - 100, t + 2.1)

    # Spinning rings
    cx, cy = w // 2, h // 2
    _draw_arc(frame, cx, cy, 90,  int(t*60)%360,         260, C,  1)
    _draw_arc(frame, cx, cy, 90, (int(t*60)+270)%360,     60, C,  2)
    _draw_arc(frame, cx, cy, 76, -int(t*40)%360,         200, C2, 1)
    _draw_arc(frame, cx, cy, 76, (-int(t*40)+210)%360,   100, C2, 2)
    _draw_arc(frame, cx, cy, 62,  int(t*80)%360,          90, C3, 1)
    for r in [4, 10, 22]: cv2.circle(frame, (cx, cy), r, C, 1)
    for dx, dy in [(-36,-1),( 14,-1)]: cv2.line(frame,(cx+dx*0+(dx>0)*0,cy),(cx+dx+14*(dx>0 and 1 or -1),cy),C,1)
    # Simplified crosshair
    cv2.line(frame, (cx-36,cy),(cx-14,cy), C, 1)
    cv2.line(frame, (cx+14,cy),(cx+36,cy), C, 1)
    cv2.line(frame, (cx,cy-36),(cx,cy-14), C, 1)
    cv2.line(frame, (cx,cy+14),(cx,cy+36), C, 1)
    cv2.circle(frame, (cx,cy), int(3+2*math.sin(t*4)), C, -1)

    # Top bar
    cv2.rectangle(frame, (0,0), (w,44), (0,0,0), -1)
    cv2.rectangle(frame, (0,44), (w,46), C, -1)
    cv2.putText(frame,
        "◈  Q U A N T U M   B L E N D E R   N E U R A L   C O N T R O L  ◈",
        (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.46, C, 1)
    if int(t*6) % 2 == 0:
        cv2.circle(frame, (w-60,24), 6, (0,0,255), -1)
        cv2.putText(frame, "REC", (w-48,28), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0,0,255), 1)

    # Bottom bar
    cv2.rectangle(frame, (0,h-48), (w,h), (0,0,0), -1)
    cv2.rectangle(frame, (0,h-50), (w,h-48), C, -1)
    ts = time.strftime("STARDATE %Y.%j  |  %H:%M:%S.") + f"{_hud_frame%100:02d}"
    cv2.putText(frame, ts, (10,h-28), cv2.FONT_HERSHEY_SIMPLEX, 0.44, C, 1)
    cv2.putText(frame,
        f"LAT 17.°N  |  QUANTUM SYNC: {int(97+2*math.sin(t)):02d}%  |  NEURAL LINK: ACTIVE",
        (10, h-10), cv2.FONT_HERSHEY_SIMPLEX, 0.35, DIM, 1)

    # Left panel
    px, py, pw, ph = 28, 70, 190, 220
    region = frame[py:py+ph, px:px+pw]
    frame[py:py+ph, px:px+pw] = (region * 0.35).astype(np.uint8)
    cv2.rectangle(frame, (px,py), (px+pw,py+ph), C, 1)
    cv2.line(frame, (px,py+18), (px+pw,py+18), DIM, 1)
    cv2.putText(frame, "◈ NEURAL METRICS", (px+6,py+13),
                cv2.FONT_HERSHEY_SIMPLEX, 0.38, C, 1)
    metrics = [
        ("SYNC",  abs(math.sin(t*0.7))),
        ("FLUX",  abs(math.cos(t*1.1))),
        ("PHASE", abs(math.sin(t*1.5))),
        ("QUBIT", abs(math.cos(t*0.4))),
    ]
    for i, (lbl, val) in enumerate(metrics):
        _energy_bar(frame, px+8, py+28+i*36, pw-16, val, lbl, C)
        cv2.putText(frame, f"{int(val*100):03d}%", (px+pw-38, py+36+i*36),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.3, C2, 1)
    tx2, ty2 = px+pw//2, py+ph-30
    for i in range(3):
        a1 = t*1.5 + i*2.09;  a2 = t*1.5 + (i+1)*2.09
        p1 = (int(tx2+22*math.cos(a1)), int(ty2+22*math.sin(a1)))
        p2 = (int(tx2+22*math.cos(a2)), int(ty2+22*math.sin(a2)))
        cv2.line(frame, p1, p2, C3, 1)

    # Right panel (object telemetry)
    rpx, rpy, rpw, rph = w-210, 70, 200, 260
    region2 = frame[rpy:rpy+rph, rpx:rpx+rpw]
    frame[rpy:rpy+rph, rpx:rpx+rpw] = (region2 * 0.35).astype(np.uint8)
    cv2.rectangle(frame, (rpx,rpy), (rpx+rpw,rpy+rph), C2, 1)
    cv2.line(frame, (rpx,rpy+18), (rpx+rpw,rpy+18), DIM, 1)
    cv2.putText(frame, "◈ OBJECT TELEMETRY", (rpx+4,rpy+13),
                cv2.FONT_HERSHEY_SIMPLEX, 0.35, C2, 1)
    if obj:
        fields = [
            ("ENTITY",   obj.name[:10]),
            ("VEC-X",    f"{obj.location.x:+.3f}"),
            ("VEC-Y",    f"{obj.location.y:+.3f}"),
            ("VEC-Z",    f"{obj.location.z:+.3f}"),
            ("SCALE",    f"{obj.scale.x:.4f}"),
            ("ROT-Y",    f"{math.degrees(obj.rotation_euler.y):+.2f}°"),
            ("ROT-X",    f"{math.degrees(obj.rotation_euler.x):+.2f}°"),
            ("MASS-SIM", f"{obj.scale.x*3.14:.3f} kg"),
        ]
        for i, (k, v) in enumerate(fields):
            cv2.putText(frame, f"{k:<9} {v}", (rpx+8, rpy+34+i*24),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.34, C2, 1)
    else:
        cv2.putText(frame, "NO TARGET LOCKED", (rpx+16, rpy+100),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.36, (0,80,80), 1)

    # Hand status (bottom-centre)
    bx, by = w//2-100, h-46
    cv2.rectangle(frame, (bx,by), (bx+200,by+40), (0,0,0), -1)
    cv2.rectangle(frame, (bx,by), (bx+200,by+40),
                  C if hand_detected else DIM, 1)
    htxt = "▣ GESTURE LOCK: ON" if hand_detected else "○ SCANNING HANDS..."
    cv2.putText(frame, htxt, (bx+10,by+25), cv2.FONT_HERSHEY_SIMPLEX,
                0.42, C if hand_detected else (0,80,80), 1)

    # Corner decorations
    for (ex,ey),(dx,dy) in [
        ((0,0),(1,1)), ((w,0),(-1,1)),
        ((0,h),(1,-1)), ((w,h),(-1,-1))
    ]:
        for s in [30,50,70]:
            thick = 2 if s == 70 else 1
            cv2.line(frame, (ex,ey), (ex+dx*s, ey), C, thick)
            cv2.line(frame, (ex,ey), (ex, ey+dy*s), C, thick)
        cv2.circle(frame, (ex+dx*14, ey+dy*14), 4, C3, 1)

    # Alert if no hand
    if not hand_detected and int(t*3) % 2 == 0:
        cv2.putText(frame, "  AWAITING NEURAL INPUT  ",
                    (cx-120, cy+110), cv2.FONT_HERSHEY_SIMPLEX,
                    0.5, (0,80,200), 1)
   
    
    # ── Phase 1 overlays ─────────────────────────────────────────
    _update_mood()
    _draw_mood_border(frame, t)
    _draw_fps_counter(frame, t)
    _draw_poly_counter(frame, obj, t)
    _draw_command_log(frame, t)
    _draw_status_ticker(frame, t)

    # ── Existing overlays (keep these) ───────────────────────────
    _draw_scene_objects(frame, t)
    _draw_voice_waveform(frame, t)
    _draw_hud_messages(frame, t)
    _draw_help_menu(frame, t)
    _draw_model_thumbnail(frame, obj, t)

    # ── Phase 2 overlays ─────────────────────────────────────────
    _draw_timeline_hud(frame, t)
    _draw_eye_cursor(frame, t)
    _draw_viewport_mode_indicator(frame, t)
    _draw_alert_hud(frame, t)
    # ── Phase 3 overlays ─────────────────────────────────────────
    _draw_ai_status(frame, t)
    _draw_ar_overlay(frame, obj, t)
# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN BLENDER TIMER LOOP
# ═══════════════════════════════════════════════════════════════════════════════
def control_loop():
    """Registered as a Blender timer — runs on the main thread every ~10 ms."""
    global prev_two_hand_dist, _last_autosave

    if not running:
        return None     # unregister timer

    # Process any queued commands first
    result = process_commands()
    # ── Auto-save ────────────────────────────────────────────────
    if time.time() - _last_autosave > AUTOSAVE_INTERVAL:
        try:
            bpy.ops.wm.save_mainfile(filepath="/Users/harinathrayala/Desktop/plymodel/autosave.blend")
            _last_autosave = time.time()
            hud_message("◈ AUTO-SAVED", duration=3.0)
            print("[AUTOSAVE] File saved.")
        except Exception as e:
            print(f"[AUTOSAVE ERROR] {e}")
    if result is None:
        return None
    
    # ── Camera / gesture section ──────────────────────────────────────────────
    if not (_cap and CV2_OK and MP_OK and _hands_detector):
        return 0.01

    ret, frame = _cap.read()
    if not ret:
        return 0.01

    frame = cv2.flip(frame, 1)
    obj   = active()

    # MediaPipe hand detection
    rgb    = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    result = _hands_detector.process(rgb)

    # Phase 2 — face tracking
    _process_face(frame, rgb)
    # Phase 3 — pose + dual hand + AR
    _process_pose(frame, rgb, obj)
    if result.multi_hand_landmarks:
            _process_dual_hand(result, obj)
    _process_ai_queue()
    if result.multi_hand_landmarks:
        lm = result.multi_hand_landmarks[0].landmark

        # Phase 2 — viewport mode check
        if _viewport_mode:
            _process_viewport_gesture(
                result.multi_hand_landmarks[0], obj)
        elif obj:

            # Pinch to scale
            if math.dist((lm[4].x, lm[4].y), (lm[8].x, lm[8].y)) < 0.04:
                s = obj.scale.x + PINCH_SCALE_SPEED
                obj.scale = (s, s, s)

            # Palm position → XZ move
            tx = (lm[9].x - 0.5) * MOVE_SENS
            tz = (0.5 - lm[9].y)  * MOVE_SENS
            obj.location.x = lerp(obj.location.x, tx, SMOOTH)
            obj.location.z = lerp(obj.location.z, tz, SMOOTH)

            # Wrist–palm vector → rotation
            obj.rotation_euler.y = lerp(obj.rotation_euler.y,
                                        (lm[0].x - lm[9].x) * math.pi, SMOOTH)
            obj.rotation_euler.x = lerp(obj.rotation_euler.x,
                                        (lm[0].y - lm[9].y) * math.pi, SMOOTH)

            # Two-hand spread → Y depth (zoom)
            if len(result.multi_hand_landmarks) == 2:
                lm2 = result.multi_hand_landmarks[1].landmark
                d   = math.dist((lm[9].x, lm[9].y), (lm2[9].x, lm2[9].y))
                if prev_two_hand_dist is not None:
                    obj.location.y += (d - prev_two_hand_dist) * MOVE_SENS
                prev_two_hand_dist = d
            else:
                prev_two_hand_dist = None

    hand_detected = result.multi_hand_landmarks is not None

    # Draw HUD
    draw_scifi_hud(frame, obj, hand_detected)

    # macOS: imshow must be called from main thread — this is fine inside a timer
    cv2.imshow("QUANTUM BLENDER NEURAL CONTROL", frame)

    # ESC → quit
    key = cv2.waitKey(1) & 0xFF
    if key == 27:
        command_queue.put(("QUIT", None))

    return 0.01

# ═══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════
# Unregister any previous instance (safe to run "Run Script" multiple times)
if bpy.app.timers.is_registered(control_loop):
    bpy.app.timers.unregister(control_loop)

bpy.app.timers.register(control_loop, first_interval=0.1)
speak("AI Blender Controller Activated")
print("=" * 60)
print("  QUANTUM BLENDER NEURAL CONTROL — macOS Edition")
print(f"  Camera:   {'OK' if _cap else 'DISABLED'}")
print(f"  Gestures: {'OK' if MP_OK else 'DISABLED'}")
print(f"  Voice:    {'OK' if VOSK_OK else 'DISABLED'}")
print(f"  JARVIS:   LOCAL MODE — No API key needed")
print("=" * 60)
