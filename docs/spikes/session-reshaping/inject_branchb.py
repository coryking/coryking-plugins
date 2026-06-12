import json, uuid
D="/Users/coryking/.claude/projects/-Users-coryking-projects-coryking-plugins"
SESSION="0422be7b-ccd0-40d9-b5db-00f4cca4127b"
BR=f"{D}/5b66e511-30cc-417a-8f12-9e469d35e7db.jsonl"
NEW_AGENT="a11b22c33d44e55f6"

# pull full content of the turns we want, by uuid, from branch-b
want = ["71ac21e3","fcef14f1","79396bcc","95d351e3","997e9e1b","7c7dd6af","f6eee38f","e81d1b66"]
bytext={}
for l in open(BR):
    d=json.loads(l)
    u=d.get("uuid","")
    if u[:8] in want:
        msg=d["message"]; role=msg["role"]; cont=msg["content"]
        if isinstance(cont,list):
            txt="".join(b.get("text","") for b in cont if isinstance(b,dict) and b.get("type")=="text")
        else:
            txt=cont
        bytext[u[:8]]=(role,txt)

ts="2026-06-12T14:30:00.000Z"
lines=[]; parent=None
for short in want:
    role,txt = bytext[short]
    u=str(uuid.uuid4())
    if role=="user":
        line={"parentUuid":parent,"isSidechain":True,"agentId":NEW_AGENT,"type":"user",
              "message":{"role":"user","content":txt},"uuid":u,"timestamp":ts,
              "userType":"external","entrypoint":"cli","cwd":"/Users/coryking/projects/coryking-plugins",
              "sessionId":SESSION,"version":"2.1.175","gitBranch":"main"}
        if parent is None:
            line["promptId"]=str(uuid.uuid4())
    else:
        line={"parentUuid":parent,"isSidechain":True,"agentId":NEW_AGENT,
              "message":{"model":"claude-opus-4-8","id":"msg_"+uuid.uuid4().hex[:24],"type":"message",
                         "role":"assistant","content":[{"type":"text","text":txt}],
                         "stop_reason":"end_turn","stop_sequence":None,
                         "usage":{"input_tokens":100,"output_tokens":50,"service_tier":"standard"}},
              "requestId":"req_"+uuid.uuid4().hex[:20],"attributionAgent":"general-purpose",
              "type":"assistant","uuid":u,"timestamp":ts,"userType":"external","entrypoint":"cli",
              "cwd":"/Users/coryking/projects/coryking-plugins","sessionId":SESSION,
              "version":"2.1.175","gitBranch":"main"}
    lines.append(line); parent=u

outdir=f"{D}/{SESSION}/subagents"
outjsonl=f"{outdir}/agent-{NEW_AGENT}.jsonl"
outmeta=f"{outdir}/agent-{NEW_AGENT}.meta.json"
with open(outjsonl,"w") as f:
    for ln in lines: f.write(json.dumps(ln)+"\n")
with open(outmeta,"w") as f:
    json.dump({"agentType":"general-purpose","description":"branch-b injected session","toolUseId":"toolu_"+uuid.uuid4().hex[:24]},f)

print("WROTE",outjsonl)
print("lines:",len(lines))
print("first line parentUuid:",lines[0]["parentUuid"]," last role:",lines[-1]["type"])
print("last assistant text:",lines[-1]["message"]["content"][0]["text"][:160])
