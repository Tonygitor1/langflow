import type { UseMutationResult } from "@tanstack/react-query";
import type { useMutationFunctionType } from "@/types/api";
import { api } from "../../api";
import { getURL } from "../../helpers/constants";
import { UseRequestProcessor } from "../../services/request-processor";

interface IDeployMarketplace {
  flowId: string;
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
    const response = await api.post(
      `${getURL("FLOWS")}/${payload.flowId}/deploy-marketplace`,
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
