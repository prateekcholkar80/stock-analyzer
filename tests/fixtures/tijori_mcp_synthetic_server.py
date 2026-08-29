"""Synthetic MCP stdio server; never imports or contacts Tijori."""

import json
import os
from pathlib import Path
import sys
import time


TOOLS = (
    "search_company",
    "resolve_company_ids",
    "get_company_overview",
    "get_financials",
    "get_shareholding",
)


def send(message):
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def response(request_id, result):
    send({"jsonrpc": "2.0", "id": request_id, "result": result})


session_path = Path(os.environ["JARVIS_TIJORI_SESSION_FILE"])
mode = json.loads(session_path.read_text(encoding="utf-8")).get("mode", "normal")
contract = os.environ["JARVIS_TIJORI_PROVIDER_CONTRACT_VERSION"]

for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    request_id = request.get("id")

    if method == "initialize":
        if "TIJORI_PASSWORD" in os.environ:
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32603, "message": "environment leak"},
                }
            )
            continue
        protocol = request["params"]["protocolVersion"]
        if mode == "wrong_protocol":
            protocol = "1900-01-01"
        provider_contract = (
            "tijori.synthetic_contract.changed"
            if mode == "wrong_contract"
            else contract
        )
        response(
            request_id,
            {
                "protocolVersion": protocol,
                "capabilities": {
                    "tools": {},
                    "experimental": {
                        "jarvisTijori": {
                            "authenticated": mode != "unauthenticated",
                            "providerContractVersion": provider_contract,
                        }
                    },
                },
                "serverInfo": {"name": "synthetic", "version": "1.0.0"},
            },
        )
        continue

    if method == "notifications/initialized":
        continue

    if mode == "hang":
        time.sleep(5)
        continue
    if mode == "malformed_json":
        sys.stdout.write("not-json\n")
        sys.stdout.flush()
        continue
    if mode == "oversized_message":
        sys.stdout.write("{\"padding\":\"" + ("x" * 1_200_000) + "\"}\n")
        sys.stdout.flush()
        continue
    if mode == "notification_first":
        send({"jsonrpc": "2.0", "method": "notifications/progress"})
        mode = "normal"
    if mode == "rpc_authentication":
        send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": -32001,
                    "message": "sessionid=must-not-leak",
                },
            }
        )
        continue
    if mode == "mismatched_id":
        response(999, {})
        continue

    if method == "tools/list":
        tools = list(TOOLS)
        if mode == "unapproved_tool":
            tools.append("fetch_document")
        response(
            request_id,
            {"tools": [{"name": name, "inputSchema": {}} for name in tools]},
        )
        continue

    if method == "tools/call":
        tool_name = request["params"]["name"]
        envelope_tool = (
            "resolve_company_ids" if mode == "wrong_tool" else tool_name
        )
        if mode == "semantic_not_found":
            envelope = {
                "tool_name": envelope_tool,
                "status": "not_found",
                "payload": None,
            }
        elif mode == "tool_error_success_envelope":
            envelope = {
                "tool_name": envelope_tool,
                "status": "success",
                "payload": {"companies": []},
            }
        else:
            payload = (
                {"companies": []}
                if tool_name == "search_company"
                else {"synthetic": True}
            )
            envelope = {
                "tool_name": envelope_tool,
                "status": "success",
                "payload": payload,
            }
        response(
            request_id,
            {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(envelope, separators=(",", ":")),
                    }
                ],
                "isError": mode == "tool_error_success_envelope",
            },
        )
        continue

    send(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": "method not found"},
        }
    )
