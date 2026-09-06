import type { UseMutationResult } from "@tanstack/react-query";
import type { useMutationFunctionType } from "@/types/api";
import { api } from "../../api";
import { getURL } from "../../helpers/constants";
import { UseRequestProcessor } from "../../services/request-processor";

export type SsoRegisterType = {
  email: string;
  password: string;
  first_name: string;
  last_name: string;
  role: "producer" | "consumer";
};

export type SsoRegisterResponse = {
  email_verification_sent: boolean;
};

export const useSsoRegister: useMutationFunctionType<
  undefined,
  SsoRegisterType
> = (options?) => {
  const { mutate } = UseRequestProcessor();

  const ssoRegisterFunction = async (
    payload: SsoRegisterType,
  ): Promise<SsoRegisterResponse> => {
    const res = await api.post(`${getURL("SSO_REGISTER")}`, payload);
    return res.data;
  };

  const mutation: UseMutationResult<SsoRegisterResponse, any, SsoRegisterType> =
    mutate(["useSsoRegister"], ssoRegisterFunction, options);

  return mutation;
};
