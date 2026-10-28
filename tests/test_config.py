import pytest

from shootagent.config import Settings, anthropic_client_kwargs


def settings(**kw):
    base = dict(anthropic_api_key=None, anthropic_auth_token=None, anthropic_base_url=None)
    return Settings(_env_file=None, **{**base, **kw})


@pytest.mark.parametrize(
    "env, expected",
    [
        # gateway token with its gateway: the token goes to the gateway
        (dict(anthropic_auth_token="gw", anthropic_base_url="http://127.0.0.1:8317"),
         {"auth_token": "gw", "base_url": "http://127.0.0.1:8317"}),
        # both set, no gateway URL: the API key goes to Anthropic, the gateway token goes nowhere
        (dict(anthropic_auth_token="gw", anthropic_api_key="key"),
         {"api_key": "key", "base_url": "https://api.anthropic.com"}),
        # a blanked gateway (serve-public.sh) behaves as unset
        (dict(anthropic_auth_token="", anthropic_base_url="", anthropic_api_key="key"),
         {"api_key": "key", "base_url": "https://api.anthropic.com"}),
        # an API key with a proxy URL goes to the proxy
        (dict(anthropic_api_key="key", anthropic_base_url="https://proxy.example"),
         {"api_key": "key", "base_url": "https://proxy.example"}),
    ],
)
def test_anthropic_credentials_go_only_to_their_own_endpoint(env, expected):
    assert anthropic_client_kwargs(settings(**env)) == expected


def test_a_gateway_token_without_a_gateway_url_is_refused():
    # sending a gateway's bearer token to api.anthropic.com would leak it to the wrong party
    with pytest.raises(RuntimeError, match="ANTHROPIC_BASE_URL"):
        anthropic_client_kwargs(settings(anthropic_auth_token="gw"))
