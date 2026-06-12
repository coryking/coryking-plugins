import json, uuid, sys
# session -> subagent, stamping OWNED provenance fields (_from_session_id, _lineage).
# argv1 = source session guid
D="/Users/coryking/.claude/projects/-Users-coryking-projects-coryking-plugins"
PARENT="0422be7b-ccd0-40d9-b5db-00f4cca4127b"
SRC=sys.argv[1]
NEW_AGENT="a"+uuid.uuid4().hex[:16]
src=f"{D}/{SRC}.jsonl"

# read any existing lineage breadcrumb from the source (so chains accumulate, not reset)
prior_lineage=None
for l in open(src):
    d=json.loads(l)
    if "_lineage" in d: prior_lineage=d["_lineage"]; break
lineage=(prior_lineage or [])+[{"as":"session","id":SRC},{"as":"subagent","id":NEW_AGENT}]

STRIP=("forkedFrom","isMeta","attributionAgent","attributionSkill","sourceToolAssistantUUID")
chain=[]; parent=None
for l in open(src):
    d=json.loads(l)
    if d.get("type") not in ("user","assistant"): continue
    if not isinstance(d.get("message",{}),dict): continue
    for k in STRIP: d.pop(k,None)
    d["isSidechain"]=True; d["agentId"]=NEW_AGENT; d["sessionId"]=PARENT
    d["parentUuid"]=parent
    u=d.get("uuid") or str(uuid.uuid4()); d["uuid"]=u; parent=u
    d["_from_session_id"]=SRC          # <-- owned marker
    d["_lineage"]=lineage              # <-- owned breadcrumb
    chain.append(d)

outdir=f"{D}/{PARENT}/subagents"
with open(f"{outdir}/agent-{NEW_AGENT}.jsonl","w") as f:
    for ln in chain: f.write(json.dumps(ln)+"\n")
with open(f"{outdir}/agent-{NEW_AGENT}.meta.json","w") as f:
    json.dump({"agentType":"general-purpose","description":"branch-b gauntlet (provenance-tagged)","toolUseId":"toolu_"+uuid.uuid4().hex[:24]},f)

print("NEW_AGENT_ID:",NEW_AGENT)
print("turns:",len(chain))
print("_from_session_id stamped:",chain[0]["_from_session_id"])
print("_lineage:",json.dumps(lineage))
