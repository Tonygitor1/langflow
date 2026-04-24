import * as Form from "@radix-ui/react-form";
import { useQueryClient } from "@tanstack/react-query";
import { useContext, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import LangflowLogo from "@/assets/LangflowLogo.svg?react";
import { useLoginUser } from "@/controllers/API/queries/auth";
import { CustomLink } from "@/customization/components/custom-link";
import { useSanitizeRedirectUrl } from "@/hooks/use-sanitize-redirect-url";
import InputComponent from "../../components/core/parameterRenderComponent/components/inputComponent";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { CONTROL_LOGIN_STATE, IS_AUTO_LOGIN } from "../../constants/constants";
import { AuthContext } from "../../contexts/authContext";
import useAlertStore from "../../stores/alertStore";
import type { LoginType } from "../../types/api";
import type {
  inputHandlerEventType,
  loginInputStateType,
} from "../../types/components";

// Module-level cache — survives React 18 StrictMode double-invoke
let _oidcEnabledCache: boolean | undefined;

export default function LoginPage(): JSX.Element {
  const [inputState, setInputState] =
    useState<loginInputStateType>(CONTROL_LOGIN_STATE);

  const { password, username } = inputState;

  useSanitizeRedirectUrl();

  const { t } = useTranslation();
  const { login, clearAuthSession } = useContext(AuthContext);
  const setErrorData = useAlertStore((state) => state.setErrorData);
  const [oidcEnabled, setOidcEnabled] = useState<boolean>(
    _oidcEnabledCache === true,
  );

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

  useEffect(() => {
    if (_oidcEnabledCache === true) return;
    let cancelled = false;
    const poll = (attempt: number) => {
      fetch("/api/v1/login/oidc/config", { headers: {} })
        .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
        .then((d) => {
          if (d?.enabled === true) {
            _oidcEnabledCache = true;
            if (!cancelled) setOidcEnabled(true);
          }
        })
        .catch(() => {
          // Retry up to 5 times (2 s apart) to handle backend still starting up
          if (!cancelled && attempt < 5) {
            setTimeout(() => poll(attempt + 1), 2000);
          }
        });
    };
    poll(0);
    return () => {
      cancelled = true;
    };
  }, []);

  function handleInput({
    target: { name, value },
  }: inputHandlerEventType): void {
    setInputState((prev) => ({ ...prev, [name]: value }));
  }

  const { mutate } = useLoginUser();
  const queryClient = useQueryClient();

  function signIn() {
    const user: LoginType = {
      username: username.trim(),
      password: password.trim(),
    };

    mutate(user, {
      onSuccess: (data) => {
        clearAuthSession();
        login(data.access_token, "login", data.refresh_token);
        queryClient.clear();
      },
      onError: (error) => {
        setErrorData({
          title: t("errors.signin"),
          list: [error["response"]["data"]["detail"]],
        });
      },
    });
  }

  return (
    <Form.Root
      onSubmit={(event) => {
        if (password === "") {
          event.preventDefault();
          return;
        }
        signIn();
        const _data = Object.fromEntries(new FormData(event.currentTarget));
        event.preventDefault();
      }}
      className="h-screen w-full"
    >
      <div className="flex h-full w-full flex-col items-center justify-center bg-muted">
        <div className="flex w-72 flex-col items-center justify-center gap-2">
          <LangflowLogo
            title="Langflow logo"
            className="mb-4 h-10 w-10 scale-[1.5]"
          />
          <span className="mb-6 text-2xl font-semibold text-primary">
            {t("auth.loginTitle")}
          </span>
          <div className="mb-3 w-full">
            <Form.Field name="username">
              <Form.Label className="data-[invalid]:label-invalid">
                {t("auth.usernameLabel")}{" "}
                <span className="font-medium text-destructive">*</span>
              </Form.Label>

              <Form.Control asChild>
                <Input
                  type="username"
                  onChange={({ target: { value } }) => {
                    handleInput({ target: { name: "username", value } });
                  }}
                  value={username}
                  className="w-full"
                  required
                  placeholder={t("auth.usernamePlaceholder")}
                />
              </Form.Control>

              <Form.Message match="valueMissing" className="field-invalid">
                {t("auth.usernameRequired")}
              </Form.Message>
            </Form.Field>
          </div>
          <div className="mb-3 w-full">
            <Form.Field name="password">
              <Form.Label className="data-[invalid]:label-invalid">
                {t("auth.passwordLabel")}{" "}
                <span className="font-medium text-destructive">*</span>
              </Form.Label>

              <InputComponent
                onChange={(value) => {
                  handleInput({ target: { name: "password", value } });
                }}
                value={password}
                isForm
                password={true}
                required
                placeholder={t("auth.passwordPlaceholder")}
                className="w-full"
              />

              <Form.Message className="field-invalid" match="valueMissing">
                {t("auth.passwordRequired")}
              </Form.Message>
            </Form.Field>
          </div>
          <div className="w-full">
            <Form.Submit asChild>
              <Button className="mr-3 mt-6 w-full" type="submit">
                {t("auth.signInButton")}
              </Button>
            </Form.Submit>
          </div>
          <div className="w-full">
            <CustomLink to="/signup">
              <Button className="w-full" variant="outline" type="button">
                {t("auth.noAccount")}&nbsp;<b>{t("auth.signUpLink")}</b>
              </Button>
            </CustomLink>
          </div>
          {oidcEnabled && (
            <div className="w-full mt-2">
              <a href="/api/v1/login/oidc/authorize" className="w-full">
                <Button
                  className="w-full"
                  variant="outline"
                  type="button"
                  data-testid="keycloak-sso-btn"
                >
                  Sign in with Keycloak
                </Button>
              </a>
            </div>
          )}
        </div>
      </div>
    </Form.Root>
  );
}
