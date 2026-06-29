import type { UseMutationResult } from "@tanstack/react-query";
import type { useMutationFunctionType } from "@/types/api";
import { api } from "../../api";
import { getURL } from "../../helpers/constants";
import { UseRequestProcessor } from "../../services/request-processor";

export interface A2ASkill {
  id: string;
  name: string;
  description: string;
  tags: string[];
  examples?: string[];
  inputModes?: string[];
  outputModes?: string[];
}

export interface A2AConfig {
  name: string;
  description?: string;
  version?: string;
  capabilities?: Record<string, boolean>;
  authentication?: Record<string, unknown>;
  defaultInputModes?: string[];
  defaultOutputModes?: string[];
  skills?: A2ASkill[];
  provider?: { organization: string; url: string } | null;
  documentationUrl?: string | null;
}

interface IDeployMarketplace {
  flowId: string;
  a2a_config: A2AConfig;
}

export interface DeployMarketplaceResponse {
  agent_id: string;
  container_url: string;
  status: string;
  flow_id: string;
}

export const usePostDeployMarketplace: useMutationFunctionType<
  undefined,
  IDeployMarketplace
> = (options?) => {
  const { mutate } = UseRequestProcessor();

  const postDeployMarketplaceFn = async (
    payload: IDeployMarketplace,
  ): Promise<DeployMarketplaceResponse> => {
    const { flowId, ...body } = payload;
    const response = await api.post(
      `${getURL("FLOWS")}/${flowId}/deploy-marketplace`,
      body,
    );
    return response.data;
  };

  const mutation: UseMutationResult<
    DeployMarketplaceResponse,
    any,
    IDeployMarketplace
  > = mutate(["usePostDeployMarketplace"], postDeployMarketplaceFn, options);

  return mutation;
};
