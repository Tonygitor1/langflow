import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import LangflowLogo from "@/assets/LangflowLogo.svg?react";
import { useSanitizeRedirectUrl } from "@/hooks/use-sanitize-redirect-url";
import { Button } from "../../components/ui/button";
import useAlertStore from "../../stores/alertStore";

// Keycloak SSO is the only supported sign-in path for the builder, and only
// producers/admins get in. Consumers and signup belong to the marketplace; the
// backend knows its URL and redirects.
const SSO_AUTHORIZE_URL = "/api/v1/login/oidc/authorize";
const MARKETPLACE_LOGIN_URL = "/api/v1/login/oidc/marketplace?path=login";
const MARKETPLACE_SIGNUP_URL = "/api/v1/login/oidc/marketplace?path=signup";

export default function LoginPage(): JSX.Element {
  useSanitizeRedirectUrl();

  const { t } = useTranslation();
  const setErrorData = useAlertStore((state) => state.setErrorData);

  // Show SSO errors as an alert. Errors arrive either via:
  //   • ?sso_error= URL param  — Keycloak-originated errors (user cancelled, etc.)
  //   • sso_error cookie       — backend access-denied (avoids polluting the
  //                              post_logout_redirect_uri with query params)
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const urlErr = params.get("sso_error");
    if (urlErr) {
      setErrorData({ title: urlErr, list: [] });
      params.delete("sso_error");
      const clean =
        window.location.pathname +
        (params.toString() ? "?" + params.toString() : "");
      window.history.replaceState({}, "", clean);
      return;
    }
    const cookieEntry = document.cookie
      .split(";")
      .find((c) => c.trim().startsWith("sso_error="));
    if (cookieEntry) {
      const cookieErr = decodeURIComponent(
        cookieEntry.split("=").slice(1).join("=").trim(),
      );
      setErrorData({ title: cookieErr, list: [] });
      document.cookie = "sso_error=; max-age=0; path=/";
    }
  }, []);

  return (
    <div className="flex h-screen w-full flex-col items-center justify-center bg-muted">
      <div className="flex w-72 flex-col items-center justify-center gap-2">
        <LangflowLogo
          title="Langflow logo"
          className="mb-4 h-10 w-10 scale-[1.5]"
        />
        <span className="mb-6 text-2xl font-semibold text-primary">
          {t("auth.loginTitle")}
        </span>
        <div className="w-full">
          <Button
            className="w-full"
            type="button"
            data-testid="keycloak-sso-btn"
            onClick={() => {
              window.location.assign(SSO_AUTHORIZE_URL);
            }}
          >
            {t("auth.signInAsProducer")}
          </Button>
        </div>
        <div className="w-full">
          <Button
            className="w-full"
            variant="outline"
            type="button"
            data-testid="marketplace-login-btn"
            onClick={() => {
              window.location.assign(MARKETPLACE_LOGIN_URL);
            }}
          >
            {t("auth.signInAsConsumer")}
          </Button>
        </div>
        <p className="mt-4 text-sm text-muted-foreground">
          {t("auth.noAccount")}{" "}
          <button
            type="button"
            className="font-semibold text-primary underline-offset-4 hover:underline"
            data-testid="marketplace-signup-btn"
            onClick={() => {
              window.location.assign(MARKETPLACE_SIGNUP_URL);
            }}
          >
            {t("auth.signUpLink")}
          </button>
        </p>
      </div>
    </div>
  );
}
