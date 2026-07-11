import type { UseQueryResult } from "@tanstack/react-query";
import type { useQueryFunctionType } from "@/types/api";
import type { A2AConfig } from "@/controllers/API/queries/flows/use-post-deploy-marketplace";
import { api } from "../../api";
import { getURL } from "../../helpers/constants";
import { UseRequestProcessor } from "../../services/request-processor";

export interface DeploymentStatusResponse {
  // "not_deployed" | "starting" | "ready" | "failed" | "stopped" | "unknown"
  status: string;
  flow_id?: string;
  agent_id?: string;
  version?: string | null;
  last_error?: string | null;
  deployed_at?: string | null;
  a2a_config?: A2AConfig;
  argocd?: {
    version?: string | null;
    sync?: string | null;
    health?: string | null;
  } | null;
}

interface GetDeploymentStatusParams {
  flowId: string | null | undefined;
}

/**
 * True while the deployment is still settling: either the executor is still
 * bringing it up (`starting`), or the pod is up (`ready`) but ArgoCD hasn't
 * finished rolling out yet (health `Progressing`). We keep showing "loading"
 * and keep polling until the row is `ready` AND ArgoCD health is `Healthy`.
 */
export function isDeploymentInProgress(
  data?: DeploymentStatusResponse,
): boolean {
  if (!data) return false;
  if (data.status === "starting") return true;
  if (data.status === "ready" && data.argocd?.health === "Progressing") {
    return true;
  }
  return false;
}

export const useGetDeploymentStatus: useQueryFunctionType<
  GetDeploymentStatusParams,
  DeploymentStatusResponse
> = (params, options?) => {
  const { query } = UseRequestProcessor();

  const getStatusFn = async (): Promise<DeploymentStatusResponse> => {
    const res = await api.get(
      `${getURL("FLOWS")}/${params?.flowId}/deployment-status`,
    );
    return res.data;
  };

  const queryResult: UseQueryResult<DeploymentStatusResponse, any> = query(
    ["useGetDeploymentStatus", params?.flowId],
    getStatusFn,
    {
      enabled: !!params?.flowId,
      // Poll while the deploy is still settling (starting, or ArgoCD still
      // Progressing); stop once it's fully ready/healthy or terminal.
      refetchInterval: (q) => {
        const data = q.state.data as DeploymentStatusResponse | undefined;
        return isDeploymentInProgress(data) ? 3000 : false;
      },
      refetchOnWindowFocus: false,
      ...options,
    },
  );

  return queryResult;
};
