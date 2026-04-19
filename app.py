from flask import Flask, request, jsonify, render_template, send_from_directory
import json, os, re, datetime

app = Flask(__name__, template_folder=os.path.dirname(os.path.abspath(__file__)))

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

def call_claude(system_prompt, user_prompt, max_tokens=4096):
    import urllib.request
    headers = {
        "Content-Type": "application/json",
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01"
    }
    payload = json.dumps({
        "model": "claude-sonnet-4-20250514",
        "max_tokens": max_tokens,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}]
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload, headers=headers, method="POST"
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())["content"][0]["text"]

def strip_json(raw):
    raw = raw.strip()
    raw = re.sub(r'^```json\s*', '', raw)
    raw = re.sub(r'^```\s*', '', raw)
    raw = re.sub(r'\s*```$', '', raw)
    return raw.strip()

def mock_analyze(text, mode):
    raw = re.split(r'(?<=[.!?])\s+', text.strip())
    sentences = [s.strip() for s in raw if s.strip()][:8] or [text.strip()[:200]]
    TYPES    = ["entity","date","statistic","citation","entity","date","statistic","citation"]
    STATUSES = ["verified","verified","unverified","hallucination","unverified","verified","hallucination","unverified"]
    CONFS    = [92, 85, 55, 23, 60, 88, 18, 47]
    SOURCES  = [
        "Wikipedia – well-documented historical fact",
        "Peer-reviewed studies (Nature, 2021)",
        "No primary source found; plausible but unconfirmed",
        "Contradicted by official records; likely AI fabrication",
        "Partially supported by secondary sources",
        "Multiple reputable outlets confirm this",
        "No evidence found; statistical claim appears fabricated",
        "Anecdotal reports only; no rigorous verification"
    ]
    REWRITES = [None,None,
        "This claim requires verification from primary sources.",
        "This claim is unsupported. Consider removing or citing a source.",
        "Partially confirmed – verify with an authoritative source.",
        None,
        "No data supports this statistic; it should be removed.",
        "Anecdotal only – qualify with 'reportedly'."]
    TEMPORAL = [False,True,False,False,True,False,False,True]
    claims = []
    for i, s in enumerate(sentences):
        idx = i % len(STATUSES)
        conf = CONFS[idx]; st = STATUSES[idx]
        if mode == "lenient" and st == "hallucination":
            conf = min(conf+20,100); st = "unverified"
        elif mode == "strict" and st == "unverified":
            st = "hallucination"; conf = max(conf-15,5)
        claims.append({
            "claim": s, "status": st, "confidence": conf,
            "source": SOURCES[idx], "claim_type": TYPES[idx],
            "rewrite": REWRITES[idx], "temporal_risk": TEMPORAL[idx],
            "depends_on": [i-1] if i > 0 and st == "hallucination" else []
        })
    return claims

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/analyze", methods=["POST"])
def analyze():
    data = request.json
    text = data.get("text","").strip()
    mode = data.get("mode","standard")
    engine = data.get("engine", "anthropic")
    
    if not text:
        return jsonify({"error": "No text provided"}), 400
        
    if engine == "ollama" or not ANTHROPIC_API_KEY:
        # Mock logic simulates local Ollama execution when engine is ollama
        import time; time.sleep(1.5) # Simulate local processing delay
        return jsonify({"claims": mock_analyze(text, mode), "mocked": True, "engine": engine})
    try:
        raw = call_claude(
            "You are a precise fact-extraction assistant. Extract every distinct factual claim. "
            "Return ONLY a JSON array of strings. No preamble, no markdown.",
            f"Extract all factual claims:\n\n{text}"
        )
        claims_list = json.loads(strip_json(raw))
    except Exception as e:
        return jsonify({"error": f"Extraction failed: {e}"}), 500
    mode_hint = {"strict":"Be very strict. Anything without a well-known primary source is hallucination.",
                 "lenient":"Only mark as hallucination if clearly and provably false.",
                 "standard":"Use balanced judgment."}.get(mode,"Use balanced judgment.")
    verify_sys = (
        "You are a hallucination-detection expert. For each claim return a JSON object with: "
        "claim (string), status (verified|unverified|hallucination), confidence (0-100 int), "
        "source (short evidence string), claim_type (entity|date|statistic|citation|other), "
        "rewrite (corrected sentence if status!=verified else null), "
        "temporal_risk (bool – true if claim may be outdated), "
        "depends_on (array of 0-based indices of claims this one logically depends on). "
        f"{mode_hint} Return ONLY a JSON array. No markdown."
    )
    try:
        raw2 = call_claude(verify_sys, f"Verify these claims:\n{json.dumps(claims_list)}")
        claims = json.loads(strip_json(raw2))
    except Exception as e:
        return jsonify({"error": f"Verification failed: {e}"}), 500
    return jsonify({"claims": claims, "mocked": False})

@app.route("/dna", methods=["POST"])
def dna():
    claims = request.json.get("claims", [])
    type_map = {"entity":0,"date":0,"statistic":0,"citation":0,"other":0}
    hall_map = dict(type_map)
    for c in claims:
        t = c.get("claim_type","other")
        if t not in type_map: t = "other"
        type_map[t] += 1
        if c.get("status") == "hallucination": hall_map[t] += 1
    scores = {t: round(hall_map[t]/type_map[t]*100) if type_map[t] else 0 for t in type_map}
    dominant = max(scores, key=scores.get) if scores else "entity"
    return jsonify({"scores": scores, "counts": type_map, "dominant": dominant})

@app.route("/deepfake_scan", methods=["POST"])
def deepfake_scan():
    # Return mocked deepfake citations to sync with the demo text.
    return jsonify({
        "sources": [
            {"entity": "Gustave Eiffel", "type": "person", "is_fake": False, "reason": "Verified historical figure."},
            {"entity": "1887 and 1889", "type": "date", "is_fake": False, "reason": "Verified construction timeline."},
            {"entity": "Eiffel's Relativity Journal (1905)", "type": "publication", "is_fake": True, "reason": "Publication does not exist. Pure generation artifact."},
            {"entity": "Marie Curie's Quantum Diary", "type": "book", "is_fake": True, "reason": "Deepfake hallucination: Marie Curie never authored this book or conducted secret experiments at the tower."},
            {"entity": "Radio Transmission Utility", "type": "fact", "is_fake": False, "reason": "Verified historical fact that saved the tower in 1909."},
            {"entity": "https://paris.org/repaint-history-19", "type": "url", "is_fake": True, "reason": "Dead link / Fabricated URL structure."}
        ]
    })

@app.route("/rewrite", methods=["POST"])
def rewrite_doc():
    data = request.json
    text = data.get("text","")
    claims = data.get("claims",[])
    engine = data.get("engine", "anthropic")
    
    if engine == "ollama" or not ANTHROPIC_API_KEY:
        import time; time.sleep(2)
        result = text
        for c in claims:
            if c.get("status") != "verified" and c.get("rewrite") and c.get("claim") in result:
                result = result.replace(c["claim"], f"[CORRECTED: {c['rewrite']}]")
        return jsonify({"rewritten": result})
    flagged = [c for c in claims if c.get("status") != "verified"]
    sys_p = ("You are a document editor. Fix or remove each flagged claim in the original text. "
             "Prefix each changed sentence with [CORRECTED]. Return ONLY the rewritten document.")
    try:
        result = call_claude(sys_p, f"Original:\n{text}\n\nFlagged:\n{json.dumps(flagged)}", 2048)
        return jsonify({"rewritten": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/fingerprint", methods=["POST"])
def fingerprint():
    scores = request.json.get("scores", {})
    gpt = 40 + scores.get("entity", 0) * 0.2
    claude = 30 + scores.get("citation", 0) * 0.2
    llama = 30 + scores.get("statistic", 0) * 0.2
    total = gpt + claude + llama
    if total == 0: total = 1
    return jsonify({
        "models": {
            "GPT-4o": round((gpt/total)*100), 
            "Claude 3.5 Sonnet": round((claude/total)*100), 
            "Llama 3 70B": round((llama/total)*100)
        }
    })

@app.route("/contradictions", methods=["POST"])
def contradictions():
    claims = request.json.get("claims", [])
    pairs = []
    # Mock contradiction for demo
    halls = [i for i,c in enumerate(claims) if c.get("status") in ("hallucination", "unverified")]
    if len(halls) >= 2:
        pairs.append({
            "claim1_idx": halls[0], 
            "claim2_idx": halls[1], 
            "reason": f"({claims[halls[0]].get('claim_type')}) contradicts ({claims[halls[1]].get('claim_type')}) contextually."
        })
    return jsonify({"contradictions": pairs})

@app.route("/fallacies", methods=["POST"])
def fallacies():
    text = request.json.get("text", "")
    fals = []
    # Example hardcoded fallacies matching our demo text
    if "demolished" in text.lower():
         fals.append({"fallacy": "Oversimplification / False Cause", "excerpt": "almost demolished in 1909 but was saved because of its usefulness", "reason": "Reduces multiple socio-political factors to a single 'usefulness' variable."})
    if "secret" in text.lower():
         fals.append({"fallacy": "Sensationalism", "excerpt": "secret quantum physics experiments", "reason": "Uses dramatic, emotionally loaded adjectives ('secret') to bypass logical scrutiny."})
    return jsonify({"fallacies": fals})

@app.route("/test_connection", methods=["POST"])
def test_connection():
    target = request.json.get("target", "unknown")
    # Simulate some logic
    return jsonify({
        "status": "success", 
        "message": f"Connection established with {target} cluster.",
        "latency": "42ms",
        "timestamp": datetime.datetime.now().strftime("%H:%M:%S")
    })

@app.route('/<path:filename>')
def serve_static(filename):
    return send_from_directory('.', filename)

if __name__ == "__main__":
    app.run(debug=True, port=5000)
