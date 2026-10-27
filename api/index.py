# idlescan Desk — Vercel guardrail entrypoint
#
# idlescan Desk is a self-hosted, local-first CLI tool. It is NOT designed to be
# deployed as a public web service. This file exists only to satisfy Vercel's
# Python entrypoint requirement and to refuse any request made to a public
# deployment, so nobody mistakes this for a hosted service.

from http import HTTPStatus


def handler(request):
    """Refuse to serve idlescan on a public platform."""
    body = (
        "idlescan Desk is a self-hosted local tool, not a public web service.\n"
        "Do not deploy it to Vercel or any public hosting platform.\n"
        "\n"
        "Security model: idlescan runs on your own machine, binds to loopback by\n"
        "default, keeps its SQLite database locally, and never holds keys or signs\n"
        "transactions. Exposing it publicly defeats every one of those guarantees.\n"
        "\n"
        "Run it locally instead:\n"
        "  python -m pip install idlescan\n"
        "  idlescan serve          # http://127.0.0.1:8787\n"
        "  idlescan serve --demo   # synthetic data, zero network\n"
    )
    return {
        "statusCode": int(HTTPStatus.FORBIDDEN),
        "headers": {"Content-Type": "text/plain; charset=utf-8"},
        "body": body,
    }
