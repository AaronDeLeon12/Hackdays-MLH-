import streamlit as st
import streamlit.components.v1 as components
import chess
import chess.svg
import chess.pgn
from stockfish import Stockfish
from dotenv import load_dotenv
from google import genai
from google.genai import types
from st_click_detector import click_detector
import base64
import os
import platform
import time
import math
import random

# -----------------------------
# 1. PAGE SETUP
# -----------------------------
st.set_page_config(layout="wide", page_title="Gemini Chess Coach")

st.markdown(
    """
    <style>
    .block-container {
        padding-top: 2rem;
        padding-bottom: 1rem;
        max-width: 1500px;
    }

    section[data-testid="stSidebar"] {
        min-width: 230px;
        max-width: 260px;
    }

    div[data-testid="stVerticalBlock"] {
        gap: 0.6rem;
    }

    .stChatMessage {
        padding-top: 0.35rem;
        padding-bottom: 0.35rem;
    }

    .small-note {
        height: 24px;
        font-size: 14px;
        opacity: 0.75;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

load_dotenv()
my_api_key = os.getenv("GEMINI_API_KEY")

if my_api_key is None:
    st.error("🚨 I cannot find the API key. Make sure your .env file is named exactly '.env' and is in the correct folder.")
    st.stop()

client = genai.Client(api_key=my_api_key)

GEMINI_MODEL = "gemini-3-flash-preview"
ENGINE_DELAY_SECONDS = 1.4

# -----------------------------
# 2. STOCKFISH SETUP
# -----------------------------
def get_stockfish_path():
    if platform.system() == "Windows":
        return r"C:\Users\aaron\source\repos\chessGemini\chessGemini\stockfish\stockfish-windows-x86-64-avx2.exe"
    return "stockfish"


if "board" not in st.session_state:
    st.session_state.board = chess.Board()

if "selected_square" not in st.session_state:
    st.session_state.selected_square = None

if "engine_pending" not in st.session_state:
    st.session_state.engine_pending = False

if "black_first_move_started" not in st.session_state:
    st.session_state.black_first_move_started = False

if "pending_sound" not in st.session_state:
    st.session_state.pending_sound = None

if "coach_queue" not in st.session_state:
    st.session_state.coach_queue = []

if "last_click_processed" not in st.session_state:
    st.session_state.last_click_processed = None

if "post_game_report" not in st.session_state:
    st.session_state.post_game_report = None

if "eval_cache" not in st.session_state:
    st.session_state.eval_cache = {}

if "last_player_move_text" not in st.session_state:
    st.session_state.last_player_move_text = ""

if "last_personality" not in st.session_state:
    st.session_state.last_personality = None

if "last_intensity" not in st.session_state:
    st.session_state.last_intensity = None

if "stockfish_player" not in st.session_state:
    st.session_state.stockfish_player = Stockfish(path=get_stockfish_path())

if "stockfish_coach" not in st.session_state:
    st.session_state.stockfish_coach = Stockfish(path=get_stockfish_path())
    st.session_state.stockfish_coach.set_skill_level(20)

# -----------------------------
# 3. SIDEBAR SETTINGS
# -----------------------------
st.sidebar.title("⚙️ Game Settings")

personality = st.sidebar.selectbox(
    "🧠 Coach Personality",
    [
        "Witty & Casual",
        "Aggressive Attacker",
        "Defensive Mastermind",
        "Grandmaster Analyst",
    ],
)

coach_intensity = st.sidebar.radio(
    "🔥 Coach Intensity",
    ["Kind", "Balanced", "Savage"],
)

use_gemini_auto = st.sidebar.checkbox(
    "Use Gemini after engine moves",
    value=False,
    help="Off = faster and no quota problems. On = Gemini comments after engine moves.",
)

greetings = {
    "Witty & Casual": "Hey there! Ready to play? Let's keep it fun—and try not to hang your queen on move two!",
    "Aggressive Attacker": "Alright, warrior. We’re here to attack, pressure, and make the enemy king regret logging in.",
    "Defensive Mastermind": "Welcome. Calm hands, safe king, clean structure. Let them make the mistakes first.",
    "Grandmaster Analyst": "Greetings. We shall examine the position with elegance, precision, and only mild emotional damage.",
}

if "chat_history" not in st.session_state:
    st.session_state.chat_history = [{"role": "assistant", "content": greetings[personality]}]

if st.session_state.last_personality is None:
    st.session_state.last_personality = personality

if st.session_state.last_intensity is None:
    st.session_state.last_intensity = coach_intensity

if (
    personality != st.session_state.last_personality
    or coach_intensity != st.session_state.last_intensity
):
    st.session_state.last_personality = personality
    st.session_state.last_intensity = coach_intensity
    st.session_state.chat_history.append(
        {
            "role": "assistant",
            "content": f"Mode updated: {personality} / {coach_intensity}. New comments will use this style.",
        }
    )

if st.sidebar.button("🔄 Restart Game", type="primary", use_container_width=True):
    st.session_state.board = chess.Board()
    st.session_state.selected_square = None
    st.session_state.engine_pending = False
    st.session_state.black_first_move_started = False
    st.session_state.pending_sound = None
    st.session_state.coach_queue = []
    st.session_state.last_click_processed = None
    st.session_state.post_game_report = None
    st.session_state.eval_cache = {}
    st.session_state.last_player_move_text = ""
    st.session_state.chat_history = [{"role": "assistant", "content": greetings[personality]}]
    st.rerun()

st.sidebar.divider()

elo_slider = st.sidebar.slider(
    "🤖 Stockfish Difficulty (Elo)",
    min_value=1320,
    max_value=3190,
    value=1500,
    step=10,
)

game_in_progress = len(st.session_state.board.move_stack) > 0

player_color = st.sidebar.radio(
    "♟️ Play as:",
    ["White", "Black"],
    disabled=game_in_progress,
)

# -----------------------------
# 4. GEMINI COACH SETUP
# -----------------------------
base_instruction = """You are a chess coach inside an interactive chess app.

The user is a beginner/intermediate player.
You will receive:
1. What just happened.
2. The current board state in FEN.
3. The Stockfish evaluation.
4. The selected personality and intensity.

Rules:
- Keep responses short: maximum 3 sentences.
- Be helpful and practical.
- Explain ideas in simple language.
- Do not give the exact best move unless the user asks.
- If the intensity is Kind, be warm and encouraging.
- If the intensity is Balanced, be funny but still focused.
- If the intensity is Savage, roast lightly, but never be cruel.
"""

personality_prompts = {
    "Witty & Casual": "Use casual humor, simple chess language, and playful comments.",
    "Aggressive Attacker": "Focus on threats, initiative, sacrifices, king attacks, and pressure.",
    "Defensive Mastermind": "Focus on king safety, opponent threats, structure, and avoiding blunders.",
    "Grandmaster Analyst": "Use deeper positional terms, but explain them simply and clearly.",
}

intensity_prompts = {
    "Kind": "Be caring, supportive, and gentle. Encourage the player even when the move is bad.",
    "Balanced": "Be witty, honest, and useful. Add some humor without being too harsh.",
    "Savage": "Be sharper and funnier. Lightly roast weak moves, but still give useful coaching.",
}

full_instruction = f"""
{base_instruction}

Personality:
{personality_prompts[personality]}

Intensity:
{intensity_prompts[coach_intensity]}
"""

# -----------------------------
# 5. HELPER FUNCTIONS
# -----------------------------
def is_player_turn():
    board = st.session_state.board

    if board.is_game_over():
        return False

    if player_color == "White":
        return board.turn == chess.WHITE

    return board.turn == chess.BLACK


def ask_gemini(prompt, max_tokens=None):
    try:
        full_text = ""

        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=full_instruction,
                temperature=0.85,
            ),
        )

        if response.text:
            full_text += response.text.strip()

        finish_reason = ""

        try:
            finish_reason = str(response.candidates[0].finish_reason)
        except Exception:
            finish_reason = ""

        tries = 0

        while "MAX_TOKENS" in finish_reason and tries < 3:
            tries += 1

            continue_prompt = f"""
Continue exactly where you left off.
Do not restart.
Do not summarize.
Finish the same answer naturally.

Previous answer:
{full_text}
"""

            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=continue_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=full_instruction,
                    temperature=0.85,
                ),
            )

            if response.text:
                full_text += " " + response.text.strip()

            try:
                finish_reason = str(response.candidates[0].finish_reason)
            except Exception:
                finish_reason = ""

        if full_text:
            return full_text.strip()

        return "I got no response from Gemini."

    except Exception as error:
        return f"Coach error: {error}"


def get_eval_raw():
    fen = st.session_state.board.fen()

    if fen in st.session_state.eval_cache:
        return st.session_state.eval_cache[fen]

    try:
        st.session_state.stockfish_coach.set_fen_position(fen)
        eval_info = st.session_state.stockfish_coach.get_evaluation()
        st.session_state.eval_cache[fen] = eval_info
        return eval_info

    except Exception:
        return {"type": "cp", "value": 0}


def get_eval_white_pov():
    board = st.session_state.board
    eval_info = get_eval_raw()

    if eval_info.get("type") == "mate":
        mate_value = eval_info.get("value", 0)

        if board.turn == chess.BLACK:
            mate_value = -mate_value

        return {
            "type": "mate",
            "value": mate_value,
            "text": f"Mate in {mate_value}",
        }

    cp = eval_info.get("value", 0)

    if board.turn == chess.BLACK:
        cp = -cp

    return {
        "type": "cp",
        "value": cp,
        "text": f"{cp / 100:+.2f}",
    }


def get_eval_text():
    return get_eval_white_pov()["text"]


def append_assistant_message(text):
    st.session_state.chat_history.append(
        {
            "role": "assistant",
            "content": text,
        }
    )

    if len(st.session_state.chat_history) > 18:
        st.session_state.chat_history = st.session_state.chat_history[-18:]


def get_move_traits(move, board_before):
    piece = board_before.piece_at(move.from_square)
    piece_name = "piece"

    if piece:
        piece_names = {
            chess.PAWN: "pawn",
            chess.KNIGHT: "knight",
            chess.BISHOP: "bishop",
            chess.ROOK: "rook",
            chess.QUEEN: "queen",
            chess.KING: "king",
        }
        piece_name = piece_names.get(piece.piece_type, "piece")

    is_capture = board_before.is_capture(move)
    is_castle = board_before.is_castling(move)

    board_before.push(move)
    gives_check = board_before.is_check()
    is_game_over = board_before.is_game_over()
    board_before.pop()

    to_square = chess.square_name(move.to_square)
    from_square = chess.square_name(move.from_square)

    center_squares = ["d4", "e4", "d5", "e5"]

    return {
        "piece": piece_name,
        "from": from_square,
        "to": to_square,
        "capture": is_capture,
        "check": gives_check,
        "castle": is_castle,
        "game_over": is_game_over,
        "center": to_square in center_squares,
    }


def build_move_flavor(traits):
    parts = []

    if traits["capture"]:
        parts.append("capture")
    if traits["check"]:
        parts.append("check")
    if traits["castle"]:
        parts.append("king safety")
    if traits["center"]:
        parts.append("center")

    if not parts:
        parts.append(traits["piece"])

    return ", ".join(parts)


def get_style_profile():
    profiles = {
        "Witty & Casual": {
            "voice": "casual, funny, friendly, slightly sarcastic",
            "focus": ["simple plans", "piece activity", "not blundering", "center control"],
            "energy": {
                "Kind": "warm and playful",
                "Balanced": "honest, funny, and practical",
                "Savage": "light roast, witty, but still helpful",
            },
            "verbs": ["pokes", "questions", "tests", "nudges", "challenges", "annoys"],
            "images": ["the board", "your plan", "the position", "that idea", "your setup"],
        },

        "Aggressive Attacker": {
            "voice": "sharp, energetic, attacking, fearless",
            "focus": ["initiative", "king pressure", "open lines", "tempo", "active pieces"],
            "energy": {
                "Kind": "encouraging and energetic",
                "Balanced": "bold and competitive",
                "Savage": "intense, witty, and slightly ruthless",
            },
            "verbs": ["pressures", "attacks", "hunts", "provokes", "forces", "threatens"],
            "images": ["the king", "your attack", "the initiative", "the pressure", "the battlefield"],
        },

        "Defensive Mastermind": {
            "voice": "calm, strategic, secure, controlled",
            "focus": ["king safety", "weak squares", "structure", "threat prevention", "solid plans"],
            "energy": {
                "Kind": "calm and supportive",
                "Balanced": "practical and cautious",
                "Savage": "dry, defensive, and lightly mocking bad risks",
            },
            "verbs": ["stabilizes", "blocks", "controls", "limits", "protects", "restrains"],
            "images": ["your king", "the structure", "the defense", "the weak squares", "your safety net"],
        },

        "Grandmaster Analyst": {
            "voice": "analytical, precise, elegant, slightly dramatic",
            "focus": ["piece coordination", "structure", "activity", "long-term weaknesses", "tactical justification"],
            "energy": {
                "Kind": "clear and thoughtful",
                "Balanced": "precise with some wit",
                "Savage": "intellectual roast with useful analysis",
            },
            "verbs": ["reframes", "questions", "clarifies", "tests", "refines", "exposes"],
            "images": ["the position", "the structure", "your concept", "the coordination", "the tactical details"],
        },
    }

    return profiles[personality]


def describe_move_theme(traits):
    themes = []

    if traits["castle"]:
        themes.append("king safety")
    if traits["capture"]:
        themes.append("material tension")
    if traits["check"]:
        themes.append("direct pressure")
    if traits["center"]:
        themes.append("central control")

    if not themes:
        piece_themes = {
            "pawn": "space and structure",
            "knight": "piece activity",
            "bishop": "diagonal pressure",
            "rook": "open-file potential",
            "queen": "major-piece activity",
            "king": "king safety",
            "piece": "piece coordination",
        }
        themes.append(piece_themes.get(traits["piece"], "piece coordination"))

    return random.choice(themes)


def get_eval_mood():
    evaluation = get_eval_white_pov()

    if evaluation["type"] == "mate":
        value = evaluation["value"]

        if value > 0:
            return "winning"
        elif value < 0:
            return "danger"
        return "unclear"

    cp = evaluation["value"]

    if player_color == "Black":
        cp = -cp

    if cp > 120:
        return "good"
    if cp < -120:
        return "bad"
    return "unclear"


def build_personality_comment(actor, move_text, traits):
    style = get_style_profile()
    theme = describe_move_theme(traits)
    eval_mood = get_eval_mood()

    verb = random.choice(style["verbs"])
    image = random.choice(style["images"])
    focus = random.choice(style["focus"])
    energy = style["energy"][coach_intensity]

    piece = traits["piece"]
    from_sq = traits["from"]
    to_sq = traits["to"]

    if actor == "player":
        opener_options = {
            "Kind": [
                f"Okay, {move_text}.",
                f"Nice, {move_text}.",
                f"I see the idea with {move_text}.",
                f"That {piece} move from {from_sq} to {to_sq} makes sense.",
            ],
            "Balanced": [
                f"{move_text}. Interesting.",
                f"{move_text}. That changes the conversation.",
                f"Alright, {move_text}.",
                f"That {piece} just stepped into the story.",
            ],
            "Savage": [
                f"{move_text}. Bold choice.",
                f"{move_text}. The board noticed.",
                f"That {piece} move has confidence.",
                f"{move_text}. Now we find out if it was vision or vibes.",
            ],
        }

        if eval_mood == "good":
            judgment_options = [
                f"It improves your chances because it works around {theme}.",
                f"It gives your position more life and helps with {focus}.",
                f"The idea is useful because it {verb} {image} instead of just waiting.",
            ]
        elif eval_mood == "bad":
            judgment_options = [
                f"The problem is that {image} may become loose if you do not follow up.",
                f"It has an idea, but your next move needs to solve the {focus} issue.",
                f"This could work, but the position may punish you if {theme} is not handled.",
            ]
        else:
            judgment_options = [
                f"The main question is whether this helps your {focus}.",
                f"It creates a small imbalance around {theme}.",
                f"Now you need to watch how {image} responds.",
            ]

        advice_options = {
            "Kind": [
                "Keep it simple and check your opponent’s most forcing reply.",
                "Good moment to breathe and look for threats before attacking.",
                "Try to improve your worst piece next.",
            ],
            "Balanced": [
                "Now calculate the annoying reply, not just the pretty one.",
                "Do not celebrate yet; chess loves charging hidden fees.",
                "Your next job is to prove this move was not just decorative.",
            ],
            "Savage": [
                "Now justify it before the board starts asking uncomfortable questions.",
                "If this was a plan, it needs a sequel immediately.",
                "Do not let this become a move that looked cool and paid rent nowhere.",
            ],
        }

    else:
        opener_options = {
            "Kind": [
                f"I answered with {move_text}.",
                f"My reply is {move_text}.",
                f"I chose {move_text}.",
                f"The engine responds with {move_text}.",
            ],
            "Balanced": [
                f"I hit back with {move_text}.",
                f"{move_text} is the reply.",
                f"The response is {move_text}.",
                f"Stockfish goes {move_text}.",
            ],
            "Savage": [
                f"{move_text}. Response delivered.",
                f"I played {move_text}.",
                f"{move_text}. The board has spoken.",
                f"The reply is {move_text}, and it is not asking politely.",
            ],
        }

        if eval_mood == "good":
            judgment_options = [
                f"It keeps your idea under control while fighting for {theme}.",
                f"It responds by improving {focus} and limiting your options.",
                f"It {verb} {image}, so your plan has to work harder now.",
            ]
        elif eval_mood == "bad":
            judgment_options = [
                f"It tries to recover control, but you may still have chances if you stay accurate.",
                f"It challenges your plan, but the position is not fully comfortable for the engine either.",
                f"It patches one issue while creating new tension around {theme}.",
            ]
        else:
            judgment_options = [
                f"It keeps the game balanced while asking questions about {theme}.",
                f"It does not win immediately, but it makes your next decision more important.",
                f"It changes the structure enough that {focus} matters more now.",
            ]

        advice_options = {
            "Kind": [
                "Look for the safest improving move.",
                "Try to keep your pieces coordinated.",
                "Do not panic; just answer the main threat.",
            ],
            "Balanced": [
                "Your plan is still alive, but now it has homework.",
                "This is where you check tactics before vibes.",
                "Find the move that improves something and does not drop anything.",
            ],
            "Savage": [
                "Your idea now needs evidence, not confidence.",
                "The board is asking for proof of concept.",
                "If your plan was real, this is where it survives cross-examination.",
            ],
        }

    opener = random.choice(opener_options[coach_intensity])
    judgment = random.choice(judgment_options)
    advice = random.choice(advice_options[coach_intensity])

    spice = ""

    if coach_intensity == "Savage":
        spice_bits = [
            "Slightly rude position, honestly.",
            "The board is not being gentle.",
            "This is where fake plans start sweating.",
            "Chess is very good at exposing optimism.",
        ]
        if random.random() < 0.35:
            spice = " " + random.choice(spice_bits)

    elif coach_intensity == "Kind":
        kind_bits = [
            "You are still doing fine.",
            "This is a useful learning moment.",
            "Stay calm and keep building.",
            "The idea is there; now refine it.",
        ]
        if random.random() < 0.30:
            spice = " " + random.choice(kind_bits)

    return f"{opener} {judgment} {advice}{spice}"


def quick_player_comment(move_text, traits):
    text = build_personality_comment("player", move_text, traits)
    append_assistant_message(text)


def quick_engine_comment(engine_move_text, traits):
    player_move = st.session_state.last_player_move_text or "your move"
    flavor = build_move_flavor(traits)

    if use_gemini_auto:
        prompt = f"""
The user played: {player_move}
The engine responded with: {engine_move_text}
Move theme: {flavor}
Current FEN: {st.session_state.board.fen()}
Evaluation from White's perspective: {get_eval_text()}
Personality: {personality}
Intensity: {coach_intensity}

Give a varied coaching comment.
Maximum 2 short sentences.
"""
        text = ask_gemini(prompt, max_tokens=120)
    else:
        text = build_personality_comment("engine", engine_move_text, traits)

    append_assistant_message(text)


def add_engine_coach_comment(event_text):
    fen = st.session_state.board.fen()
    eval_text = get_eval_text()

    prompt = f"""
What just happened:
{event_text}

Current FEN:
{fen}

Stockfish evaluation from White's point of view:
{eval_text}

Personality:
{personality}

Intensity:
{coach_intensity}

Respond as the coach after the engine move.
Explain how the engine responded to the user's idea.
Use the selected personality clearly.
Maximum 3 short sentences.
"""

    text = ask_gemini(prompt, max_tokens=260)
    append_assistant_message(text)


def process_coach_queue():
    if len(st.session_state.coach_queue) == 0:
        return

    events = st.session_state.coach_queue[:]
    st.session_state.coach_queue = []

    for event in events:
        add_engine_coach_comment(event)


def get_sound_type(move):
    board = st.session_state.board

    if board.is_capture(move):
        return "capture"

    board.push(move)
    is_check = board.is_check()
    is_game_over = board.is_game_over()
    board.pop()

    if is_game_over:
        return "gameover"

    if is_check:
        return "check"

    return "move"


def play_sound(kind):
    if not kind:
        return

    if kind == "capture":
        volume = 0.45
        thump_freq = 120
        noise_duration = 0.095
        second_hit = 0.055
    elif kind == "check":
        volume = 0.38
        thump_freq = 165
        noise_duration = 0.08
        second_hit = 0.05
    elif kind == "gameover":
        volume = 0.38
        thump_freq = 90
        noise_duration = 0.18
        second_hit = 0.075
    else:
        volume = 0.34
        thump_freq = 140
        noise_duration = 0.065
        second_hit = 0.045

    components.html(
        f"""
        <script>
        try {{
            const AudioContextClass = window.AudioContext || window.webkitAudioContext;
            const ctx = new AudioContextClass();

            async function startSound() {{
                if (ctx.state === "suspended") {{
                    await ctx.resume();
                }}

                function woodHit(delay, freq, dur, volume, filterFreq) {{
                    const now = ctx.currentTime + delay;

                    const bufferSize = Math.floor(ctx.sampleRate * dur);
                    const buffer = ctx.createBuffer(1, bufferSize, ctx.sampleRate);
                    const data = buffer.getChannelData(0);

                    for (let i = 0; i < bufferSize; i++) {{
                        const t = i / bufferSize;
                        const decay = Math.pow(1 - t, 3.5);
                        const crack = (Math.random() * 2 - 1) * decay;
                        const body = Math.sin(2 * Math.PI * freq * (i / ctx.sampleRate)) * decay * 0.55;
                        data[i] = (crack * 0.75 + body * 0.25);
                    }}

                    const source = ctx.createBufferSource();
                    source.buffer = buffer;

                    const bandpass = ctx.createBiquadFilter();
                    bandpass.type = "bandpass";
                    bandpass.frequency.value = filterFreq;
                    bandpass.Q.value = 1.1;

                    const lowpass = ctx.createBiquadFilter();
                    lowpass.type = "lowpass";
                    lowpass.frequency.value = 1800;

                    const gain = ctx.createGain();
                    gain.gain.setValueAtTime(volume, now);
                    gain.gain.exponentialRampToValueAtTime(0.001, now + dur);

                    source.connect(bandpass);
                    bandpass.connect(lowpass);
                    lowpass.connect(gain);
                    gain.connect(ctx.destination);

                    source.start(now);
                    source.stop(now + dur);
                }}

                woodHit(0.00, {thump_freq}, {noise_duration}, {volume}, 850);
                woodHit({second_hit}, {thump_freq * 0.72}, {noise_duration * 0.70}, {volume * 0.55}, 650);
            }}

            startSound();
        }} catch(e) {{
            console.log(e);
        }}
        </script>
        """,
        height=0,
    )


def get_legal_targets(square_name):
    board = st.session_state.board
    square = chess.parse_square(square_name)

    targets = []

    for move in board.legal_moves:
        if move.from_square == square:
            targets.append(move.to_square)

    return targets


def find_legal_move(from_square, to_square):
    board = st.session_state.board
    from_sq = chess.parse_square(from_square)
    to_sq = chess.parse_square(to_square)

    for move in board.legal_moves:
        if move.from_square == from_sq and move.to_square == to_sq:
            return move

    return None


def move_to_text(move):
    board = st.session_state.board
    san = board.san(move)
    return f"{san} ({move.uci()})"


def make_player_move(move):
    board_before = st.session_state.board.copy()
    move_text = move_to_text(move)
    traits = get_move_traits(move, board_before)
    sound = get_sound_type(move)

    st.session_state.board.push(move)
    st.session_state.selected_square = None
    st.session_state.pending_sound = sound
    st.session_state.last_player_move_text = move_text

    quick_player_comment(move_text, traits)

    st.session_state.engine_pending = True
    st.session_state.last_click_processed = None


def make_engine_move():
    board = st.session_state.board

    if board.is_game_over():
        return

    st.session_state.stockfish_player.set_elo_rating(elo_slider)
    st.session_state.stockfish_player.set_fen_position(board.fen())

    try:
        best_move_uci = st.session_state.stockfish_player.get_best_move_time(500)
    except Exception:
        best_move_uci = st.session_state.stockfish_player.get_best_move()

    if not best_move_uci:
        return

    move = chess.Move.from_uci(best_move_uci)

    if move not in board.legal_moves:
        return

    board_before = board.copy()
    move_text = move_to_text(move)
    traits = get_move_traits(move, board_before)
    sound = get_sound_type(move)

    board.push(move)

    st.session_state.pending_sound = sound
    st.session_state.last_click_processed = None

    if use_gemini_auto:
        st.session_state.coach_queue.append(
            f"""
The engine responded with {move_text}.
Explain how this move responds to the player's previous idea.
"""
        )
    else:
        quick_engine_comment(move_text, traits)


def handle_square_click(square_name):
    board = st.session_state.board

    if not is_player_turn():
        return

    selected = st.session_state.selected_square
    clicked_square = chess.parse_square(square_name)
    clicked_piece = board.piece_at(clicked_square)

    if selected is None:
        if clicked_piece and clicked_piece.color == board.turn:
            st.session_state.selected_square = square_name
        return

    if selected == square_name:
        st.session_state.selected_square = None
        return

    if clicked_piece and clicked_piece.color == board.turn:
        st.session_state.selected_square = square_name
        return

    move = find_legal_move(selected, square_name)

    if move:
        make_player_move(move)
    else:
        st.session_state.selected_square = None


def get_click_token():
    board = st.session_state.board
    selected = st.session_state.selected_square

    if selected is None:
        selected = "none"

    turn = "white" if board.turn == chess.WHITE else "black"

    return f"m{len(board.move_stack)}_{selected}_{turn}_{player_color}"


def render_pretty_interactive_board():
    board = st.session_state.board
    selected = st.session_state.selected_square
    flipped = player_color == "Black"

    fill = {}

    if selected:
        selected_square = chess.parse_square(selected)
        fill[selected_square] = "#f7ec6e"

        legal_targets = get_legal_targets(selected)

        for target in legal_targets:
            piece = board.piece_at(target)

            if piece:
                fill[target] = "#ff7777"
            else:
                fill[target] = "#7fc8ff"

    lastmove = None

    if len(board.move_stack) > 0:
        lastmove = board.peek()

    svg = chess.svg.board(
        board=board,
        flipped=flipped,
        fill=fill,
        lastmove=lastmove,
        size=520,
    )

    svg_b64 = base64.b64encode(svg.encode("utf-8")).decode("utf-8")

    if not flipped:
        ranks = range(7, -1, -1)
        files = range(8)
    else:
        ranks = range(8)
        files = range(7, -1, -1)

    player_can_click = is_player_turn() and not st.session_state.engine_pending
    click_token = get_click_token()

    overlay_links = ""

    for rank in ranks:
        for file in files:
            square = chess.square(file, rank)
            square_name = chess.square_name(square)

            if player_can_click:
                link_id = f"{square_name}__{click_token}"
            else:
                link_id = f"disabled__{click_token}"

            overlay_links += f"""
            <a
                class="sq-link"
                href="#"
                id="{link_id}"
                title="{square_name}"
                onclick="event.preventDefault();"
            ></a>
            """

    html = f"""
    <style>
    html, body {{
        margin: 0;
        padding: 0;
        background: transparent;
        overflow: hidden;
    }}

    .board-wrap {{
        position: relative;
        width: 520px;
        height: 520px;
        margin: 0 auto;
        border-radius: 14px;
        overflow: hidden;
        box-shadow: 0px 12px 35px rgba(0,0,0,0.38);
        background: transparent;
    }}

    .board-img {{
        width: 520px;
        height: 520px;
        display: block;
        user-select: none;
        -webkit-user-drag: none;
    }}

    .click-layer {{
        position: absolute;
        left: 0;
        top: 0;
        width: 520px;
        height: 520px;
        display: grid;
        grid-template-columns: repeat(8, 65px);
        grid-template-rows: repeat(8, 65px);
        z-index: 10;
    }}

    .sq-link {{
        display: block;
        width: 65px;
        height: 65px;
        text-decoration: none;
        cursor: pointer;
        background: rgba(255,255,255,0);
    }}

    .sq-link:hover {{
        background: rgba(255,255,255,0.10);
    }}
    </style>

    <div class="board-wrap">
        <img class="board-img" src="data:image/svg+xml;base64,{svg_b64}">
        <div class="click-layer">
            {overlay_links}
        </div>
    </div>
    """

    clicked_id = click_detector(
        html,
        key="stable_chess_board_clicker",
    )

    if clicked_id:
        if clicked_id == st.session_state.last_click_processed:
            return

        if "__" not in clicked_id:
            return

        clicked_square, received_token = clicked_id.split("__", 1)

        if received_token != click_token:
            return

        if clicked_square == "disabled":
            return

        st.session_state.last_click_processed = clicked_id

        try:
            chess.parse_square(clicked_square)
            handle_square_click(clicked_square)
            st.rerun()
        except Exception:
            pass


def render_eval_bar():
    evaluation = get_eval_white_pov()

    if evaluation["type"] == "mate":
        mate_value = evaluation["value"]

        if mate_value > 0:
            white_percent = 94
        else:
            white_percent = 6

        label = evaluation["text"]
    else:
        cp = evaluation["value"]

        white_percent = 50 + (math.tanh(cp / 600) * 45)
        white_percent = max(5, min(95, white_percent))

        label = evaluation["text"]

    black_percent = 100 - white_percent

    html = f"""
    <style>
    .eval-wrap {{
        width: 42px;
        height: 520px;
        border-radius: 12px;
        overflow: hidden;
        border: 2px solid #2a2a2a;
        box-shadow: 0px 8px 25px rgba(0,0,0,0.25);
        display: flex;
        flex-direction: column;
        background: #111;
        margin: 0 auto;
    }}

    .eval-black {{
        height: {black_percent}%;
        background: #111111;
        color: white;
        display: flex;
        align-items: flex-start;
        justify-content: center;
        font-size: 11px;
        font-weight: bold;
        padding-top: 7px;
    }}

    .eval-white {{
        height: {white_percent}%;
        background: #f2f2f2;
        color: #111;
        display: flex;
        align-items: flex-end;
        justify-content: center;
        font-size: 11px;
        font-weight: bold;
        padding-bottom: 7px;
    }}

    .eval-label {{
        text-align: center;
        font-size: 13px;
        margin-top: 8px;
        font-weight: bold;
        opacity: 0.9;
    }}
    </style>

    <div class="eval-wrap">
        <div class="eval-black">B</div>
        <div class="eval-white">W</div>
    </div>
    <div class="eval-label">{label}</div>
    """

    st.markdown(html, unsafe_allow_html=True)


def get_move_history_rows():
    temp_board = chess.Board()
    rows = []
    current_row = {}

    for index, move in enumerate(st.session_state.board.move_stack):
        move_number = index // 2 + 1
        san = temp_board.san(move)
        temp_board.push(move)

        if index % 2 == 0:
            current_row = {
                "Move": move_number,
                "White": san,
                "Black": "",
            }
        else:
            current_row["Black"] = san
            rows.append(current_row)
            current_row = {}

    if current_row:
        rows.append(current_row)

    return rows


def render_move_history():
    rows = get_move_history_rows()

    if not rows:
        st.caption("No moves yet.")
        return

    st.dataframe(
        rows,
        use_container_width=True,
        hide_index=True,
        height=240,
    )


def get_pgn_text():
    game = chess.pgn.Game()
    game.headers["Event"] = "Gemini Chess Coach Game"
    game.headers["Result"] = st.session_state.board.result() if st.session_state.board.is_game_over() else "*"

    node = game

    for move in st.session_state.board.move_stack:
        node = node.add_variation(move)

    return str(game)


def generate_post_game_report():
    result = st.session_state.board.result()
    pgn_text = get_pgn_text()

    prompt = f"""
Create a short post-game chess report.

Result:
{result}

PGN:
{pgn_text}

Personality:
{personality}

Intensity:
{coach_intensity}

Include:
1. Quick game summary.
2. Biggest turning point.
3. Best habit the player showed.
4. One mistake to fix.
5. 3 training tips.
Keep it concise, useful, and fun.
"""

    return ask_gemini(prompt, max_tokens=420)


# -----------------------------
# 6. MAIN APP
# -----------------------------
st.title("♟️ Gemini Chess Coach")

col1, col2 = st.columns([1.08, 1])

with col1:
    st.subheader("BOARD")

    if st.session_state.pending_sound:
        play_sound(st.session_state.pending_sound)
        st.session_state.pending_sound = None

    selected_text = " "
    if st.session_state.selected_square:
        selected_text = f"Selected: {st.session_state.selected_square}"

    st.markdown(
        f"""
        <div class="small-note">
            {selected_text}
        </div>
        """,
        unsafe_allow_html=True,
    )

    board_col, eval_col = st.columns([9, 1])

    with board_col:
        render_pretty_interactive_board()

    with eval_col:
        render_eval_bar()

    left_buttons, right_buttons = st.columns(2)

    with left_buttons:
        if st.button("↩️ Undo Last Full Turn", use_container_width=True):
            if len(st.session_state.board.move_stack) >= 2:
                st.session_state.board.pop()
                st.session_state.board.pop()
                st.session_state.selected_square = None
                st.session_state.engine_pending = False
                st.session_state.coach_queue = []
                st.session_state.last_click_processed = None
                st.session_state.post_game_report = None
                append_assistant_message("Undone. Time travel achieved, but your blunders still have emotional consequences.")
                st.rerun()
            else:
                st.warning("Not enough moves to undo yet.")

    st.markdown("### Move History")
    render_move_history()

    if st.session_state.board.is_game_over():
        result = st.session_state.board.result()
        st.success(f"Game Over! Result: {result}")

        if st.button("📋 Generate Post-Game Report", use_container_width=True):
            st.session_state.post_game_report = generate_post_game_report()
            st.rerun()

        if st.session_state.post_game_report:
            st.markdown("### Post-Game Report")
            st.markdown(st.session_state.post_game_report)

    if (
        player_color == "Black"
        and not st.session_state.black_first_move_started
        and len(st.session_state.board.move_stack) == 0
    ):
        st.session_state.engine_pending = True
        st.session_state.black_first_move_started = True
        st.rerun()

    if st.session_state.engine_pending and not st.session_state.board.is_game_over():
        time.sleep(ENGINE_DELAY_SECONDS)
        make_engine_move()
        st.session_state.engine_pending = False
        st.rerun()

# Process Gemini engine commentary before drawing the chat.
# This lets Gemini finish and appear without another rerun.
if len(st.session_state.coach_queue) > 0 and not st.session_state.engine_pending:
    process_coach_queue()

with col2:
    st.subheader("Coach Chat")

    chat_box = st.container(height=460, border=True)

    with chat_box:
        for message in st.session_state.chat_history[-12:]:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])

    user_msg = st.chat_input("Ask your coach for advice...")

    if user_msg:
        st.session_state.chat_history.append(
            {
                "role": "user",
                "content": user_msg,
            }
        )

        fen = st.session_state.board.fen()
        eval_text = get_eval_text()

        prompt = f"""
User message:
{user_msg}

Current FEN:
{fen}

Stockfish evaluation from White's point of view:
{eval_text}

Personality:
{personality}

Intensity:
{coach_intensity}

Answer the user's question as their chess coach.
Stay in the selected personality.
Maximum 3 short sentences unless the user asks for more detail.
Sometimes say a fun chess fact be it players origins of the game etc... 
"""

        response_text = ask_gemini(prompt, max_tokens=300)

        st.session_state.chat_history.append(
            {
                "role": "assistant",
                "content": response_text,
            }
        )

        st.rerun()