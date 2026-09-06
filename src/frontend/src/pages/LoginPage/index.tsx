import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import LangflowLogo from "@/assets/LangflowLogo.svg?react";
import { CustomLink } from "@/customization/components/custom-link";
import { useSanitizeRedirectUrl } from "@/hooks/use-sanitize-redirect-url";
import { Button } from "../../components/ui/button";
import useAlertStore from "../../stores/alertStore";

// Keycloak SSO is the only supported sign-in path for the builder.
const SSO_AUTHORIZE_URL = "/api/v1/login/oidc/authorize";

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
            {t("auth.signInButton")}
          </Button>
        </div>
        <div className="w-full">
          <CustomLink to="/signup">
            <Button className="w-full" variant="outline" type="button">
              {t("auth.noAccount")}&nbsp;<b>{t("auth.signUpLink")}</b>
            </Button>
          </CustomLink>
        </div>
      </div>
    </div>
  );
}
