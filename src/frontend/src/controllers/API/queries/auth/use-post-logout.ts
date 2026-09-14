import { LANGFLOW_AUTO_LOGIN_OPTION } from "@/constants/constants";
import useAuthStore from "@/stores/authStore";
import useFlowStore from "@/stores/flowStore";
import useFlowsManagerStore from "@/stores/flowsManagerStore";
import { useFolderStore } from "@/stores/foldersStore";
import type { useMutationFunctionType } from "@/types/api";
import { getCookiesInstance } from "@/utils/cookie-manager";
import { getAuthCookie } from "@/utils/utils";
import { api } from "../../api";
import { getURL } from "../../helpers/constants";
import { UseRequestProcessor } from "../../services/request-processor";

export const useLogout: useMutationFunctionType<undefined, void> = (
  options?,
) => {
  const { mutate, queryClient } = UseRequestProcessor();
  const cookies = getCookiesInstance();
  const logout = useAuthStore((state) => state.logout);

  async function logoutUser(): Promise<any> {
    const isSsoSession = document.cookie
      .split(";")
      .some((c) => c.trim().startsWith("sso_provider="));

    // Only the server can drop refresh_token_lf: it is HttpOnly, so
    // clearAuthCookies() cannot touch it and the next automatic /refresh would
    // silently mint a fresh access token for the user who just logged out.
    // IS_AUTO_LOGIN is deliberately not consulted — it defaults to true when the
    // frontend has no LANGFLOW_AUTO_LOGIN env var, which would skip this call.
    const isAutoLoginSession =
      useAuthStore.getState().autoLogin === true ||
      getAuthCookie(cookies, LANGFLOW_AUTO_LOGIN_OPTION) === "auto";

    if (!isAutoLoginSession) {
      await api.post(`${getURL("LOGOUT")}`);
    }

    // Ends the Keycloak session too, so the next sign-in asks for credentials.
    if (isSsoSession) {
      window.location.assign("/api/v1/login/oidc/logout");
    }
    return {};
  }

  const mutation = mutate(["useLogout"], logoutUser, {
    onSuccess: () => {
      logout();

      useFlowStore.getState().resetFlowState();
      useFlowsManagerStore.getState().resetStore();
      useFolderStore.getState().resetStore();

      // Clear all React Query cache to prevent data leakage between users
      queryClient.clear();
    },
    onError: (error) => {
      console.error(error);
    },
    ...options,
    retry: false,
  });

  return mutation;
};
