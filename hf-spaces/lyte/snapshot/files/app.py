import hashlib,json,time,urllib.request
from pathlib import Path
import httpx
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
CFG=json.loads(Path("config.json").read_text(encoding="utf-8"))
INDEX=Path("index.html").read_text(encoding="utf-8")
PANELS=Path("panels.html").read_text(encoding="utf-8")
app=FastAPI(title=CFG["title"]+" - SZL Holdings")

def sha256_text(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

def local_integrity():
    checks={
        "landing_sha256":sha256_text(INDEX)==CFG["landing_sha256"],
        "panels_sha256":sha256_text(PANELS)==CFG["panels_sha256"],
    }
    ready=all(checks.values())
    return {"schema":"szl.vertical-shell-readiness/v1","ready":ready,"state":"MEASURED" if ready else "INVALID","checks":checks,"routes":{"landing":"/","panels":"/panels","live":"/api/live","receipt":"/build-receipt.json"}}

def probe():
    started=time.time()
    try:
        r=httpx.get(CFG["upstream"],timeout=15,headers={"User-Agent":"SZLHOLDINGS-Flagship/4.0"})
        body=r.json() if "json" in r.headers.get("content-type","") else {"text":r.text[:4000]}
        return {"status":"LIVE" if r.is_success else "UNAVAILABLE","http_status":r.status_code,"latency_ms":round((time.time()-started)*1000,1),"source":CFG["upstream"],"data":body}
    except Exception as exc:
        return {"status":"UNAVAILABLE","error":type(exc).__name__,"source":CFG["upstream"],"data":None}

def hf_revision():
    try:
        req=urllib.request.Request("https://huggingface.co/api/spaces/"+CFG["hf_repository"],headers={"Accept":"application/json","User-Agent":"SZLHOLDINGS-BuildInfo/1.0"})
        with urllib.request.urlopen(req,timeout=10) as response:
            payload=json.loads(response.read().decode("utf-8"))
        value=str(payload.get("sha") or "").lower()
        return value if len(value)==40 else "UNAVAILABLE"
    except Exception:
        return "UNAVAILABLE"

@app.get("/healthz")
def healthz():
    return {"ok":True,"product":CFG["title"],"source":CFG["product_source"],"public_experience":CFG["public_experience"],"domain":CFG["slug"]}

@app.get("/readyz")
def readyz():
    status=local_integrity()
    return JSONResponse(status,status_code=200 if status["ready"] else 503)

@app.get("/api/live")
def live(): return probe()

@app.get("/api/source")
def source():
    return {"product_source":CFG["product_source"],"deployment_source":CFG["source_repository"],"source_revision":CFG["source_revision"],"workflow_run_id":CFG["workflow_run_id"]}

@app.get("/api/build-info")
def build_info():
    return JSONResponse({
        "schema":"szl.build-info/v1",
        "source_repository":CFG["source_repository"],
        "source_revision":CFG["source_revision"],
        "workflow_run_id":CFG["workflow_run_id"],
        "hf_repository":CFG["hf_repository"],
        "hf_revision":hf_revision(),
        "artifact_set_sha256":CFG["artifact_set_sha256"],
        "public_experience":CFG["public_experience"],
        "product_source":CFG["product_source"],
        "forge":CFG.get("forge"),
        "routes":{"landing":"/","panels":"/panels","live":"/api/live","receipt":"/build-receipt.json"},
    })

@app.get("/build-receipt.json")
def build_receipt():
    integrity=local_integrity()
    return JSONResponse({
        "schema":"szl.vertical-shell-deployment/v1",
        "state":"VERIFIED_RUNTIME_ARTIFACTS" if integrity["ready"] else "INVALID",
        "source_repository":CFG["source_repository"],
        "source_revision":CFG["source_revision"],
        "workflow_run_id":CFG["workflow_run_id"],
        "hf_repository":CFG["hf_repository"],
        "hf_revision":hf_revision(),
        "artifact_set_sha256":CFG["artifact_set_sha256"],
        "landing_sha256":CFG["landing_sha256"],
        "panels_sha256":CFG["panels_sha256"],
        "forge":CFG.get("forge"),
        "integrity":integrity,
    },status_code=200 if integrity["ready"] else 503)

@app.get("/.well-known/szl-source.json")
def source_document(): return build_info()

@app.get("/",response_class=HTMLResponse)
def root(): return INDEX

@app.get("/panels",response_class=HTMLResponse)
def panels(): return PANELS
