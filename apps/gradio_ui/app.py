"""Gradio display layer. Banking decisions remain in the authenticated backend."""

import json
import time
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


def proposal_controls(pending):
    expired = bool(pending and pending["expires_at"] <= time.time())
    status = pending["status"] if pending else ""
    summary = pending["summary"] if pending else ""
    if expired:
        summary += "\n\nThis proposal has expired. Cancel it to start a new request."
    return (
        summary,
        gr.update(visible=bool(pending)),
        gr.update(interactive=bool(pending and status == "prepared" and not expired)),
        gr.update(visible=bool(pending and status in ("confirmed", "verified") and not expired)),
        "Enter your development verification code below."
        if pending and status in ("confirmed", "verified") and not expired
        else "",
    )


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
        restored = {}
        if session["chat_id"]:
            try:
                restored = (
                    await bff.api(cookies(request), "GET", f"/chat/{session['chat_id']}/history")
                ).json()
            except BankError as exc:
                if exc.status == 404:
                    session["chat_id"] = None
                else:
                    raise
        bff.save(key, session)
        options = list(mapping)
        selected = next(
            (label for label, ref in mapping.items() if ref == restored.get("selected_account_id")), None
        )
        if selected is None and len(options) == 1:
            selected = options[0]
        return (
            gr.update(visible=False),
            gr.update(visible=True),
            f"### Welcome, {profile['first_name']}\n{profile['membership'].title()} membership · {profile['masked_customer_id']} · Signed in",
            gr.update(choices=options, value=selected),
            restored.get("messages", []),
            *proposal_controls(restored.get("pending_action")),
        )
    except (BankError, httpx.HTTPError):
        return (
            gr.update(visible=True),
            gr.update(visible=False),
            "",
            gr.update(choices=[], value=None),
            [],
            *proposal_controls(None),
        )


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
    reference = result.get("download_reference")
    download = (
        f'<a class="statement-link" href="/ui/statement/{reference}" target="_blank" rel="noopener">Download statement <span>CSV · Available for 5 minutes</span></a>'
        if reference
        else ""
    )
    summary, card, confirm, otp, status = proposal_controls(result.get("pending_action"))
    return (
        history,
        "",
        summary,
        rows,
        download,
        "Review your proposal below" if result.get("pending_action") else "Ready for your next question",
        card,
        gr.update(visible=bool(rows or reference)),
        confirm,
        otp,
        status,
    )


def transient(history, activity):
    # A failed/empty request must not erase a proposal or hide a pending OTP.
    return (
        history,
        "",
        gr.skip(),
        gr.skip(),
        gr.skip(),
        activity,
        gr.skip(),
        gr.skip(),
        gr.skip(),
        gr.skip(),
        gr.skip(),
    )


async def send(message, history, account, request: gr.Request):
    history = history or []
    if not message.strip():
        yield transient(history, "Type a question or choose a quick action.")
        return
    history = history + [{"role": "user", "content": redact(message)}]
    yield transient(history, "Checking your request and permissions…")
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
        yield transient(
            history + [{"role": "assistant", "content": exc.payload.safe_message}],
            "Your request could not be completed.",
        )
    except Exception:
        yield transient(
            history
            + [{"role": "assistant", "content": "The banking service is unavailable. Please try again."}],
            "Service temporarily unavailable",
        )


async def confirm(request: gr.Request):
    try:
        _, session = bff.get_session(cookies(request))
        if not session["chat_id"]:
            return "Prepare a request first.", gr.update(visible=False), gr.update(interactive=False)
        result = (await bff.api(cookies(request), "POST", f"/chat/{session['chat_id']}/confirm")).json()
        return result["message"], gr.update(visible=True), gr.update(interactive=False)
    except BankError as exc:
        return exc.payload.safe_message, gr.skip(), gr.skip()


async def verify(otp, history, request: gr.Request):
    try:
        _, session = bff.get_session(cookies(request))
        result = (
            await bff.api(
                cookies(request), "POST", f"/chat/{session['chat_id']}/verify-otp", json={"otp": otp}
            )
        ).json()
        pending = result.get("pending_action")
        summary, card, button, panel, status = proposal_controls(pending)
        return (
            "",
            history + [{"role": "assistant", "content": result["response"]}],
            status,
            panel,
            summary,
            card,
            button,
            "Submission needs another attempt" if pending else "Request submitted successfully",
        )
    except BankError as exc:
        return (
            "",
            history,
            exc.payload.safe_message,
            gr.update(visible=True),
            gr.skip(),
            gr.skip(),
            gr.skip(),
            "Check the verification code and try again.",
        )


async def cancel(history, request: gr.Request):
    try:
        _, session = bff.get_session(cookies(request))
        result = (await bff.api(cookies(request), "POST", f"/chat/{session['chat_id']}/cancel")).json()
        return (
            history + [{"role": "assistant", "content": result["response"]}],
            "",
            gr.update(visible=False),
            gr.update(visible=False),
            "Proposal cancelled",
        )
    except BankError as exc:
        return history, exc.payload.safe_message, gr.skip(), gr.skip(), "Unable to cancel this proposal."


async def clear(request: gr.Request):
    try:
        key, session = bff.get_session(cookies(request))
        if session["chat_id"]:
            await bff.api(cookies(request), "DELETE", f"/chat/{session['chat_id']}")
        session["chat_id"] = None
        session["download_reference"] = None
        bff.save(key, session)
        return (
            [],
            "",
            "",
            [],
            "",
            "Ready to help",
            gr.update(visible=False),
            gr.update(visible=False),
            gr.update(interactive=False),
            gr.update(visible=False),
            "",
        )
    except BankError as exc:
        raise gr.Error(exc.payload.safe_message) from None


def create_gradio_app():
    with gr.Blocks(title="SecureBank Agent", analytics_enabled=False, fill_width=True) as demo:
        gr.HTML(
            '<header id="brand"><div class="brand-mark" aria-hidden="true">S</div><div><h1>SecureBank<span>Agent</span></h1><p>Everyday banking, made simpler.</p></div><span class="demo-badge">SYNTHETIC DEMO</span></header>'
        )
        with gr.Column(elem_id="login-card") as login_panel:
            gr.HTML(
                '<div class="login-heading"><span class="eyebrow">YOUR BANKING SPACE</span><h2>Welcome back.</h2><p>Sign in to check your accounts and manage requests in one conversation.</p></div>'
            )
            username = gr.Textbox(value="demo01", label="Username", elem_classes=["bank-field"])
            password = gr.Textbox(type="password", label="Password", elem_classes=["bank-field"])
            login_button = gr.Button("Sign in securely", variant="primary", elem_id="login-submit")
            login_status = gr.Markdown("")
            with gr.Accordion("Try a demo account", open=True, elem_id="demo-users"):
                selector = gr.Dropdown(
                    choices=[f"demo{i:02}" for i in range(1, 13)], value="demo01", label="Demo customer"
                )
                gr.Markdown(
                    "Password: **`SyntheticDemo!42`**\n\n`demo01`–`demo10`: customers · `demo11`: support · `demo12`: administrator"
                )
            gr.Markdown(
                "Development login only. All balances, accounts, and verification codes are synthetic.",
                elem_classes=["quiet-note"],
            )
        with gr.Column(visible=False, elem_id="dashboard") as dashboard:
            with gr.Row(elem_id="session-bar"):
                with gr.Column(scale=3, min_width=200):
                    profile = gr.Markdown("", elem_id="customer-profile")
                with gr.Column(scale=1, min_width=200):
                    with gr.Row(elem_id="session-actions"):
                        clear_button = gr.Button("Clear conversation", size="sm", min_width=100)
                        logout = gr.Button("Sign out", size="sm", min_width=80)
            with gr.Row(elem_id="account-bar"):
                with gr.Column(scale=1, min_width=240):
                    account = gr.Dropdown(
                        choices=[], label="Select an account", elem_id="account-select", filterable=False
                    )
                with gr.Column(scale=2, min_width=200):
                    gr.Markdown(
                        "**Your accounts, your control**\nChoose an account for balances and transactions. Requests always need your review.",
                        elem_id="account-help",
                    )
            with gr.Accordion("Quick actions", open=False, elem_id="quick-actions"):
                suggestions = []
                options = [
                    ("Check my balance", "What is my balance?"),
                    ("Last five transactions", "Show my last five transactions"),
                    ("Generate a statement", "Generate my statement"),
                    ("Request a checkbook", "Request a checkbook"),
                    ("Report a transaction", "Report suspicious transaction 1"),
                    ("Increase credit limit", "Increase my credit limit"),
                    ("Service request status", "Check service request status"),
                    ("Explain bank charges", "Explain bank charges"),
                ]
                for start in (0, 4):
                    with gr.Row():
                        for label, text in options[start : start + 4]:
                            suggestions.append((gr.Button(label, size="sm", min_width=130), text))
            with gr.Column(elem_id="conversation-card"):
                gr.Markdown("### Banking assistant", elem_id="chat-heading")
                chatbot = gr.Chatbot(
                    label="Secure conversation",
                    show_label=False,
                    height=380,
                    elem_id="chat-window",
                    placeholder='<div class="chat-welcome"><span class="welcome-symbol" aria-hidden="true">✦</span><h3>How can I help today?</h3><p>Ask about a balance, explore your recent transactions,<br>or start a service request.</p><small>Try “Show my balance and last five transactions.”</small></div>',
                )
                activity = gr.Markdown("Ready to help", elem_id="activity")
                message = gr.Textbox(
                    label="Message",
                    show_label=False,
                    placeholder="Ask a banking question…",
                    lines=2,
                    max_lines=4,
                    elem_id="message-input",
                    container=False,
                )
                with gr.Row(elem_id="composer-actions"):
                    send_button = gr.Button("Send message", variant="primary", scale=2, min_width=140)
                    stop_button = gr.Button("Stop response", scale=1, min_width=100)
                gr.Markdown(
                    "Keep passwords and OTPs out of chat. Enter verification codes only in the separate field.",
                    elem_classes=["quiet-note"],
                )
            with gr.Accordion(
                "Transaction details & statements", open=True, visible=False, elem_id="transaction-panel"
            ) as transaction_panel:
                table = gr.Dataframe(
                    headers=["Date", "Description", "Type", "Reference", "Debit", "Credit", "Status"],
                    datatype=["str"] * 7,
                    interactive=False,
                    elem_id="transaction-table",
                )
                download = gr.HTML("")
            with gr.Column(visible=False, elem_id="action-card") as action_card:
                gr.Markdown(
                    "### Review your request\nCheck the details before confirming. Nothing is submitted until you verify the code."
                )
                proposal = gr.Markdown("")
                with gr.Row(elem_id="proposal-actions"):
                    confirm_button = gr.Button("Confirm proposal", variant="primary", interactive=False)
                    cancel_button = gr.Button("Cancel proposal")
                otp_status = gr.Markdown("")
                with gr.Column(visible=False, elem_id="otp-panel") as otp_panel:
                    otp = gr.Textbox(label="Development verification code", type="password", max_lines=1)
                    verify_button = gr.Button("Verify and submit", variant="primary")
        gr.Markdown("Synthetic banking demo · No real accounts or money transfers", elem_id="security-banner")
        gr.State(value={"theme": "banking"})
        selector.change(lambda value: value, inputs=[selector], outputs=[username], api_visibility="private")
        login_button.click(
            fn=None,
            inputs=[username, password],
            outputs=[login_status, password],
            js="""async (username,password) => {try {const r=await fetch('/ui/login',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'same-origin',body:JSON.stringify({username,password})}); const d=await r.json(); if(r.ok){window.location.reload();return ['Signed in.',''];} return [d.safe_message||'Sign-in failed.',''];}catch{return ['Unable to connect. Please try again.',''];}}""",
        )
        logout.click(
            fn=None,
            js="""async()=>{try{await fetch('/ui/logout',{method:'POST',credentials:'same-origin'});}finally{window.location.reload();}}""",
        )
        outputs = [
            chatbot,
            message,
            proposal,
            table,
            download,
            activity,
            action_card,
            transaction_panel,
            confirm_button,
            otp_panel,
            otp_status,
        ]
        send_event = send_button.click(
            send, inputs=[message, chatbot, account], outputs=outputs, api_visibility="private"
        )
        enter_event = message.submit(
            send, inputs=[message, chatbot, account], outputs=outputs, api_visibility="private"
        )
        stop_button.click(fn=None, cancels=[send_event, enter_event])
        for button, text in suggestions:
            button.click(lambda value=text: value, outputs=[message], api_visibility="private")
        confirm_button.click(
            confirm, outputs=[otp_status, otp_panel, confirm_button], api_visibility="private"
        )
        verify_button.click(
            verify,
            inputs=[otp, chatbot],
            outputs=[otp, chatbot, otp_status, otp_panel, proposal, action_card, confirm_button, activity],
            api_visibility="private",
        )
        cancel_button.click(
            cancel,
            inputs=[chatbot],
            outputs=[chatbot, proposal, otp_panel, action_card, activity],
            api_visibility="private",
        )
        clear_button.click(clear, outputs=outputs, api_visibility="private")
        demo.load(
            bootstrap,
            outputs=[
                login_panel,
                dashboard,
                profile,
                account,
                chatbot,
                proposal,
                action_card,
                confirm_button,
                otp_panel,
                otp_status,
            ],
            api_visibility="private",
        )
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
        ).set(
            block_label_background_fill="transparent",
            block_label_text_color="#5d6d7b",
            block_background_fill="white",
            button_primary_background_fill="#0b756d",
            button_primary_background_fill_hover="#095f59",
        ),
        css=css,
        show_error=False,
        allowed_paths=[],
        blocked_paths=[str(Path(".env").resolve()), str(Path(".local").resolve())],
    )
