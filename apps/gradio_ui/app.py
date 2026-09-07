"""Gradio display layer. All banking and authentication actions call FastAPI."""

import json
from pathlib import Path

import gradio as gr
import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, Response

from apps.gradio_ui.events import backend as bff
from core.config.settings import get_settings
from core.errors import BankError
from core.pii.redaction import redact


def cookies(request: gr.Request):
    return request.cookies


async def bootstrap(request: gr.Request):
    try:
        profile = (await bff.api(cookies(request), "GET", "/auth/me")).json()
        key, session = bff.get_session(cookies(request))
        try:
            accounts = (await bff.api(cookies(request), "GET", "/customers/me/accounts")).json()["accounts"]
        except BankError:
            accounts = []
        mapping = {a["kind"].title() + " · " + a["masked_account"]: a["account_id"] for a in accounts}
        session["account_map"] = mapping
        bff.save(key, session)
        options = list(mapping)
        return (
            gr.update(visible=False),
            gr.update(visible=True),
            f"### Welcome, {profile['first_name']}\n{profile['masked_customer_id']} · {profile['membership'].title()} membership · Session active",
            gr.update(choices=options, value=options[0] if len(options) == 1 else None),
        )
    except (BankError, httpx.HTTPError):
        return gr.update(visible=True), gr.update(visible=False), "", gr.update(choices=[], value=None)


def display(result, history):
    history = history + [{"role": "assistant", "content": result["response"]}]
    rows = []
    for t in result.get("transactions", []):
        amount = t["amount_paise"] / 100
        rows.append(
            [
                t["date"],
                t["description"],
                t["type"],
                t["masked_reference"],
                f"₹{-amount:,.2f}" if amount < 0 else "",
                f"₹{amount:,.2f}" if amount > 0 else "",
                t["status"],
            ]
        )
    pending = result.get("pending_action")
    summary = pending["summary"] if pending else "No action awaiting confirmation."
    reference = result.get("download_reference")
    download = (
        f'<a href="/ui/statement/{reference}" target="_blank" rel="noopener">Download your statement (expires in 5 minutes)</a>'
        if reference
        else ""
    )
    return history, "", summary, rows, download, "Request complete · " + ", ".join(result.get("agents", []))


async def send(message, history, account, request: gr.Request):
    history = history or []
    if not message.strip():
        yield history, "", "No action awaiting confirmation.", [], "", "Enter a question to get started."
        return
    sanitized = redact(message)
    history = history + [{"role": "user", "content": sanitized}]
    yield history, "", "Preparing your request…", [], "", "Understanding your request · Verifying permissions"
    try:
        key, session = bff.get_session(cookies(request))
        selected = session["account_map"].get(account)
        access = bff.cipher().decrypt(session["sealed_access"].encode()).decode()
        async with httpx.AsyncClient(base_url=get_settings().banking_api_url, timeout=60) as c:
            async with c.stream(
                "POST",
                "/chat/stream",
                headers={"Authorization": "Bearer " + access},
                json={"message": message, "session_id": session["chat_id"], "account_id": selected},
            ) as response:
                if response.status_code >= 400:
                    raise BankError(
                        "CHAT_UNAVAILABLE", "Please sign in again or try later.", response.status_code
                    )
                event = ""
                async for line in response.aiter_lines():
                    if line.startswith("event:"):
                        event = line[6:].strip()
                    if line.startswith("data:"):
                        data = json.loads(line[5:])
                        if event == "result":
                            session["chat_id"] = data["session_id"]
                            session["download_reference"] = data.get("download_reference")
                            bff.save(key, session)
                            yield display(data, history)
                        elif event == "error":
                            raise BankError("CHAT_ERROR", data.get("safe_message", "Please try again."))
    except BankError as exc:
        yield (
            history + [{"role": "assistant", "content": exc.payload.safe_message}],
            "",
            "No new action was submitted.",
            [],
            "",
            "Request could not be completed",
        )
    except Exception:
        yield (
            history
            + [{"role": "assistant", "content": "The banking service is unavailable. Please try again."}],
            "",
            "",
            [],
            "",
            "Service unavailable",
        )


async def confirm(request: gr.Request):
    try:
        _, session = bff.get_session(cookies(request))
        if not session["chat_id"]:
            return "Prepare a request first.", gr.update(visible=False)
        result = (await bff.api(cookies(request), "POST", f"/chat/{session['chat_id']}/confirm")).json()
        return result["message"], gr.update(visible=True)
    except BankError as exc:
        return exc.payload.safe_message, gr.update(visible=False)


async def verify(otp, history, request: gr.Request):
    try:
        _, session = bff.get_session(cookies(request))
        result = (
            await bff.api(
                cookies(request), "POST", f"/chat/{session['chat_id']}/verify-otp", json={"otp": otp}
            )
        ).json()
        return (
            "",
            history + [{"role": "assistant", "content": result["response"]}],
            "Verification complete. " + result["response"],
            gr.update(visible=False),
            "No action awaiting confirmation.",
        )
    except BankError as exc:
        return (
            "",
            history,
            exc.payload.safe_message,
            gr.update(visible=True),
            "Your request has not been submitted.",
        )


async def cancel(history, request: gr.Request):
    try:
        _, session = bff.get_session(cookies(request))
        result = (await bff.api(cookies(request), "POST", f"/chat/{session['chat_id']}/cancel")).json()
        return (
            history + [{"role": "assistant", "content": result["response"]}],
            "No action awaiting confirmation.",
            gr.update(visible=False),
        )
    except BankError as exc:
        return history, exc.payload.safe_message, gr.update(visible=False)


async def clear(request: gr.Request):
    try:
        key, session = bff.get_session(cookies(request))
        if session["chat_id"]:
            await bff.api(cookies(request), "DELETE", f"/chat/{session['chat_id']}")
        session["chat_id"] = None
        session["download_reference"] = None
        bff.save(key, session)
        return [], "No action awaiting confirmation.", [], "", gr.update(visible=False)
    except BankError as exc:
        raise gr.Error(exc.payload.safe_message) from None


def create_gradio_app():
    with gr.Blocks(title="SecureBank Agent", analytics_enabled=False, fill_width=True) as demo:
        gr.HTML(
            '<div id="brand"><h1>SecureBank Agent</h1><p>Your banking assistant. Clear answers. You stay in control.</p></div>'
        )
        gr.Markdown(
            "**Development simulation · Synthetic accounts only · No money transfers**",
            elem_id="security-banner",
        )
        with gr.Column(elem_id="login-card") as login_panel:
            gr.Markdown("## Welcome back\nSign in to explore your synthetic banking dashboard.")
            selector = gr.Dropdown(
                choices=[f"demo{i:02}" for i in range(1, 13)], value="demo01", label="Demo customer"
            )
            username = gr.Textbox(value="demo01", label="Username")
            password = gr.Textbox(type="password", label="Password")
            login_button = gr.Button("Sign in securely", variant="primary")
            login_status = gr.Markdown("")
            gr.Markdown(
                "Demo password: `SyntheticDemo!42`\n\nCustomers: demo01–demo10. Support: demo11. Administrator: demo12. Employee roles have no customer-account access. This built-in login is development-only."
            )
        with gr.Column(visible=False) as dashboard:
            with gr.Row():
                profile = gr.Markdown("")
                logout = gr.Button("Sign out", size="sm")
                clear_button = gr.Button("Clear conversation", size="sm")
            with gr.Row():
                with gr.Column(scale=1, min_width=240, elem_id="account-card"):
                    gr.Markdown("### Your accounts")
                    account = gr.Dropdown(choices=[], label="Select an account")
                    gr.Markdown("### How can I help?")
                    suggestions = []
                    for label, text in [
                        ("Check my balance", "What is my balance?"),
                        ("Last five transactions", "Show my last five transactions"),
                        ("Generate a statement", "Generate my statement"),
                        ("Request a checkbook", "Request a checkbook"),
                        ("Report a transaction", "Report suspicious transaction 1"),
                        ("Increase credit limit", "Increase my credit limit"),
                        ("Service request status", "Check service request status"),
                        ("Explain bank charges", "Explain bank charges"),
                    ]:
                        suggestions.append((gr.Button(label, size="sm"), text))
                with gr.Column(scale=3):
                    chatbot = gr.Chatbot(label="Secure conversation", height=430, elem_id="chat-window")
                    activity = gr.Markdown("Ready to help", elem_id="activity")
                    message = gr.Textbox(
                        label="Message",
                        placeholder="Show my balance and last five transactions",
                        lines=2,
                        max_lines=4,
                    )
                    with gr.Row():
                        send_button = gr.Button("Send message", variant="primary")
                        stop_button = gr.Button("Stop response")
                    gr.Markdown(
                        "Keep passwords, PINs, card numbers, and OTPs out of chat. Use only demo data."
                    )
            with gr.Accordion("Transaction details", open=True):
                table = gr.Dataframe(
                    headers=["Date", "Description", "Type", "Reference", "Debit", "Credit", "Status"],
                    datatype=["str"] * 7,
                    interactive=False,
                )
                download = gr.HTML("")
            with gr.Column(elem_id="action-card"):
                gr.Markdown(
                    "### Review before submitting\nConfirmation and a separate verification code are required. Credit increases and disputes go to human review."
                )
                proposal = gr.Markdown("No action awaiting confirmation.")
                with gr.Row():
                    confirm_button = gr.Button("Confirm proposal", variant="primary")
                    cancel_button = gr.Button("Cancel proposal")
                otp_status = gr.Markdown("")
                with gr.Column(visible=False) as otp_panel:
                    otp = gr.Textbox(label="Development verification code", type="password", max_lines=1)
                    verify_button = gr.Button("Verify and submit")
        # Only non-sensitive presentation state; credentials live in the server-side BFF vault.
        gr.State(value={"theme": "banking"})
        selector.change(lambda value: value, inputs=[selector], outputs=[username], api_visibility="private")
        login_button.click(
            fn=None,
            inputs=[username, password],
            outputs=[login_status, password],
            js="""async (username,password) => {const r=await fetch('/ui/login',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'same-origin',body:JSON.stringify({username,password})}); const d=await r.json(); if(r.ok){window.location.reload();return ['Signed in.',''];} return [d.safe_message||'Sign-in failed.',''];}""",
        )
        logout.click(
            fn=None,
            js="""async()=>{await fetch('/ui/logout',{method:'POST',credentials:'same-origin'});window.location.reload();}""",
        )
        outputs = [chatbot, message, proposal, table, download, activity]
        send_event = send_button.click(
            send, inputs=[message, chatbot, account], outputs=outputs, api_visibility="private"
        )
        enter_event = message.submit(
            send, inputs=[message, chatbot, account], outputs=outputs, api_visibility="private"
        )
        stop_button.click(fn=None, cancels=[send_event, enter_event])
        for button, text in suggestions:
            button.click(lambda value=text: value, outputs=[message], api_visibility="private")
        confirm_button.click(confirm, outputs=[otp_status, otp_panel], api_visibility="private")
        verify_button.click(
            verify,
            inputs=[otp, chatbot],
            outputs=[otp, chatbot, otp_status, otp_panel, proposal],
            api_visibility="private",
        )
        cancel_button.click(
            cancel, inputs=[chatbot], outputs=[chatbot, proposal, otp_panel], api_visibility="private"
        )
        clear_button.click(
            clear, outputs=[chatbot, proposal, table, download, otp_panel], api_visibility="private"
        )
        demo.load(bootstrap, outputs=[login_panel, dashboard, profile, account], api_visibility="private")
    return demo


def mount_ui(app):
    from pydantic import BaseModel, ConfigDict, Field

    class LoginInput(BaseModel):
        model_config = ConfigDict(extra="forbid")
        username: str = Field(min_length=1, max_length=64)
        password: str = Field(min_length=1, max_length=256)

    def same_origin(request):
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
            raise BankError("FORBIDDEN", "This browser request is not allowed.", 403)

    @app.post("/ui/login", include_in_schema=False)
    async def ui_login(data: LoginInput, request: Request):
        same_origin(request)
        key = await bff.login(data.username, data.password)
        response = JSONResponse({"status": "signed_in"})
        response.set_cookie(
            bff.COOKIE,
            key,
            httponly=True,
            secure=get_settings().cookie_secure,
            samesite="strict",
            max_age=900,
            path="/",
        )
        return response

    @app.post("/ui/logout", include_in_schema=False)
    async def ui_logout(request: Request):
        same_origin(request)
        try:
            key, _ = bff.get_session(request.cookies)
            await bff.api(request.cookies, "POST", "/auth/logout")
            bff.store.delete("ui:" + key)
        except BankError:
            pass
        response = JSONResponse({"status": "signed_out"})
        response.delete_cookie(bff.COOKIE, path="/")
        return response

    @app.get("/ui/statement/{reference}", include_in_schema=False)
    async def statement(reference: str, request: Request):
        result = await bff.api(request.cookies, "GET", f"/statements/{reference}/download")
        return Response(
            result.content,
            media_type="text/csv",
            headers={
                "Content-Disposition": 'attachment; filename="statement.csv"',
                "Cache-Control": "no-store",
            },
        )

    ui = create_gradio_app()
    css = Path(__file__).with_name("themes").joinpath("style.css").read_text()
    return gr.mount_gradio_app(
        app,
        ui,
        path="/assistant",
        theme=gr.themes.Soft(
            primary_hue="teal",
            neutral_hue="slate",
            font=["system-ui", "sans-serif"],
            font_mono=["ui-monospace", "monospace"],
        ),
        css=css,
        show_error=False,
        allowed_paths=[],
        blocked_paths=[str(Path(".env").resolve()), str(Path(".local").resolve())],
    )
