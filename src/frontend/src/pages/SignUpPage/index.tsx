import * as Form from "@radix-ui/react-form";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";
import LangflowLogo from "@/assets/LangflowLogo.svg?react";
import ForwardedIconComponent from "@/components/common/genericIconComponent";
import ShadTooltip from "@/components/common/shadTooltipComponent";
import InputComponent from "@/components/core/parameterRenderComponent/components/inputComponent";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import {
  type SsoRegisterType,
  useSsoRegister,
} from "@/controllers/API/queries/auth";
import { CustomLink } from "@/customization/components/custom-link";
import { useCustomNavigate } from "@/customization/hooks/use-custom-navigate";
import { track } from "@/customization/utils/analytics";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import useAlertStore from "../../stores/alertStore";

const MIN_PASSWORD_LENGTH = 8;

// Role aliases are mapped to realm roles server-side; see docs/user-registration.md.
const ROLE_OPTIONS = [
  { value: "producer", labelKey: "auth.roleProducer", hintKey: "auth.roleProducerHint" },
  { value: "consumer", labelKey: "auth.roleConsumer", hintKey: "auth.roleConsumerHint" },
] as const;

type RoleValue = (typeof ROLE_OPTIONS)[number]["value"];

/** Pydantic returns `detail` as a string for HTTPException and a list for 422. */
function readErrorDetail(error: any): string[] {
  const detail = error?.response?.data?.detail;
  if (Array.isArray(detail)) {
    return detail.map((item) => item?.msg ?? String(item));
  }
  return [detail ?? error?.message ?? ""].filter(Boolean);
}

export default function SignUp(): JSX.Element {
  const [email, setEmail] = useState<string>("");
  const [password, setPassword] = useState<string>("");
  const [firstName, setFirstName] = useState<string>("");
  const [lastName, setLastName] = useState<string>("");
  const [role, setRole] = useState<RoleValue>("producer");

  const { t } = useTranslation();
  const setSuccessData = useAlertStore((state) => state.setSuccessData);
  const setErrorData = useAlertStore((state) => state.setErrorData);
  const navigate = useCustomNavigate();

  const { mutate: mutateSsoRegister, isPending } = useSsoRegister();

  const isDisabled =
    isPending ||
    email.trim() === "" ||
    firstName.trim() === "" ||
    lastName.trim() === "" ||
    password.length < MIN_PASSWORD_LENGTH;

  function handleSignup(): void {
    const newUser: SsoRegisterType = {
      email: email.trim(),
      password,
      first_name: firstName.trim(),
      last_name: lastName.trim(),
      role,
    };

    mutateSsoRegister(newUser, {
      onSuccess: (data) => {
        track("User Signed Up", { role });
        setSuccessData({
          title: data.email_verification_sent
            ? t("auth.signUpVerifyEmail")
            : t("auth.signUpSuccess"),
        });
        navigate("/login");
      },
      onError: (error) => {
        setErrorData({
          title: t("errors.signup"),
          list: readErrorDetail(error),
        });
      },
    });
  }

  return (
    <Form.Root
      onSubmit={(event: FormEvent<HTMLFormElement>) => {
        event.preventDefault();
        if (isDisabled) {
          return;
        }
        handleSignup();
      }}
      className="h-screen w-full"
    >
      <div className="flex h-full w-full flex-col items-center justify-center overflow-auto bg-muted py-10">
        <div className="flex w-80 flex-col items-center justify-center gap-2">
          <LangflowLogo
            title="Langflow logo"
            className="mb-4 h-10 w-10 scale-[1.5]"
          />
          <span className="mb-6 text-2xl font-semibold text-primary">
            {t("auth.signupTitle")}
          </span>
          <div className="mb-3 w-full">
            <Form.Field name="email">
              <Form.Label className="data-[invalid]:label-invalid">
                {t("auth.emailLabel")}{" "}
                <span className="font-medium text-destructive">*</span>
              </Form.Label>

              <Form.Control asChild>
                <Input
                  type="email"
                  onChange={({ target: { value } }) => setEmail(value)}
                  value={email}
                  className="w-full"
                  required
                  placeholder={t("auth.emailPlaceholder")}
                />
              </Form.Control>

              <Form.Message match="valueMissing" className="field-invalid">
                {t("auth.emailRequired")}
              </Form.Message>
              <Form.Message match="typeMismatch" className="field-invalid">
                {t("auth.emailInvalid")}
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
                onChange={(value) => setPassword(value)}
                value={password}
                isForm
                password={true}
                required
                placeholder={t("auth.passwordPlaceholder")}
                className="w-full"
              />

              <Form.Message className="field-invalid" match="valueMissing">
                {t("auth.passwordEnterRequired")}
              </Form.Message>
              {password !== "" && password.length < MIN_PASSWORD_LENGTH && (
                <Form.Message className="field-invalid">
                  {t("auth.passwordTooShort", { count: MIN_PASSWORD_LENGTH })}
                </Form.Message>
              )}
            </Form.Field>
          </div>
          <div className="mb-3 w-full">
            <Form.Field name="firstName">
              <Form.Label className="data-[invalid]:label-invalid">
                {t("auth.firstNameLabel")}{" "}
                <span className="font-medium text-destructive">*</span>
              </Form.Label>

              <Form.Control asChild>
                <Input
                  type="text"
                  onChange={({ target: { value } }) => setFirstName(value)}
                  value={firstName}
                  className="w-full"
                  required
                  placeholder={t("auth.firstNamePlaceholder")}
                />
              </Form.Control>

              <Form.Message match="valueMissing" className="field-invalid">
                {t("auth.firstNameRequired")}
              </Form.Message>
            </Form.Field>
          </div>
          <div className="mb-3 w-full">
            <Form.Field name="lastName">
              <Form.Label className="data-[invalid]:label-invalid">
                {t("auth.lastNameLabel")}{" "}
                <span className="font-medium text-destructive">*</span>
              </Form.Label>

              <Form.Control asChild>
                <Input
                  type="text"
                  onChange={({ target: { value } }) => setLastName(value)}
                  value={lastName}
                  className="w-full"
                  required
                  placeholder={t("auth.lastNamePlaceholder")}
                />
              </Form.Control>

              <Form.Message match="valueMissing" className="field-invalid">
                {t("auth.lastNameRequired")}
              </Form.Message>
            </Form.Field>
          </div>
          <div className="w-full">
            <Form.Label className="data-[invalid]:label-invalid">
              {t("auth.roleLabel")}{" "}
              <span className="font-medium text-destructive">*</span>
            </Form.Label>

            <RadioGroup
              className="mt-2 gap-2"
              value={role}
              onValueChange={(value) => setRole(value as RoleValue)}
            >
              {ROLE_OPTIONS.map((option) => (
                <div
                  key={option.value}
                  className="flex items-center justify-between gap-2"
                >
                  <div className="flex items-center gap-2">
                    <RadioGroupItem
                      value={option.value}
                      id={`role-${option.value}`}
                      data-testid={`role-${option.value}`}
                    />
                    <label
                      htmlFor={`role-${option.value}`}
                      className="cursor-pointer text-sm text-primary"
                    >
                      {t(option.labelKey)}
                    </label>
                  </div>
                  <ShadTooltip
                    content={t(option.hintKey)}
                    side="right"
                    delayDuration={100}
                  >
                    <button
                      type="button"
                      aria-label={t(option.hintKey)}
                      className="text-muted-foreground hover:text-primary"
                      data-testid={`role-${option.value}-help`}
                    >
                      <ForwardedIconComponent
                        name="CircleHelp"
                        className="h-4 w-4"
                      />
                    </button>
                  </ShadTooltip>
                </div>
              ))}
            </RadioGroup>
          </div>
          <div className="w-full">
            <Form.Submit asChild>
              <Button
                disabled={isDisabled}
                type="submit"
                className="mr-3 mt-6 w-full"
              >
                {t("auth.signupButton")}
              </Button>
            </Form.Submit>
          </div>
          <div className="w-full">
            <CustomLink to="/login">
              <Button className="w-full" variant="outline" type="button">
                {t("auth.haveAccount")}&nbsp;<b>{t("auth.signInLink")}</b>
              </Button>
            </CustomLink>
          </div>
        </div>
      </div>
    </Form.Root>
  );
}
