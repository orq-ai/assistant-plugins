#!/usr/bin/env python3
"""Probe installed orq Responses routing using a local server and synthetic credentials.

Run: python3 tests/scripts/check-responses-cli.py
No remote API calls. A known CLI SSE decoding failure is reported, not hidden.
"""
import http.server, threading, subprocess, os, json
seen=[]
class Handler(http.server.BaseHTTPRequestHandler):
 def do_POST(self):
  body=json.loads(self.rfile.read(int(self.headers['Content-Length']))); seen.append((self.path,body))
  self.send_response(200)
  self.send_header('Content-Type','text/event-stream' if body.get('stream') else 'application/json');self.end_headers()
  if body.get('stream'):
   self.wfile.write(b'event: response.output_text.delta\ndata: {"type":"response.output_text.delta","delta":"Hello","item_id":"msg_synthetic","output_index":0,"content_index":0,"sequence_number":0}\n\nevent: response.completed\ndata: {"type":"response.completed","response":{"id":"resp_synthetic","status":"completed","output":[]},"sequence_number":1}\n\n')
  else: self.wfile.write(b'{"id":"resp_synthetic","object":"response","created_at":0,"model":"agent/synthetic","status":"completed","output":[{"id":"msg_synthetic","type":"message","role":"assistant","status":"completed","content":[{"type":"output_text","text":"Hello","annotations":[]}]}]}')
 def log_message(self,*args): pass
server=http.server.HTTPServer(('127.0.0.1',0),Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
env={k:v for k,v in os.environ.items() if not k.startswith('ORQ_')};env['ORQ_API_KEY']='synthetic-local-test'
base=f'http://127.0.0.1:{server.server_port}'
for extra in [[],['--previous-response-id','resp_previous'],['--stream']]:
 r=subprocess.run(['orq','responses','create','--server',base,'--model','agent/synthetic','--input','Hello','-o','json',*extra],env=env,capture_output=True,text=True,timeout=20)
 if r.returncode != 0:
  assert '--stream' in extra and 'Not sure how to unmarshal text/event-stream' in r.stderr, r.stderr
  print('CLI SSE decoding limitation: use SDK or curl -N on the same route')
 route,body=seen[-1];assert route=='/v3/router/responses',(route,body)
 assert body['model']=='agent/synthetic' and body['input']=='Hello'
 if '--previous-response-id' in extra: assert body['previous_response_id']=='resp_previous'
 if '--stream' in extra: assert body['stream'] is True
 print(json.dumps({'path':route,'body':body,'exit_code':r.returncode}))
server.shutdown()
