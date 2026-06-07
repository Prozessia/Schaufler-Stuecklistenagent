"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import { getCurrentUser, type AuthUserResponse } from "@/lib/api";
import { LOGIN_ROUTE } from "@/lib/routes";

/**
 * Single source of truth for the "is the user authenticated?" check.
 *
 * Uses one shared query key (`["auth-me"]`) so every screen reads the same
 * cached session instead of firing its own `/auth/me` request, and redirects to
 * the login page once the session is known to be invalid.
 */
export function useAuthGuard(): UseQueryResult<AuthUserResponse> {
  const router = useRouter();

  const query = useQuery({
    queryKey: ["auth-me"],
    queryFn: getCurrentUser,
    retry: false,
    refetchOnWindowFocus: false,
  });

  useEffect(() => {
    if (query.isError) {
      router.replace(LOGIN_ROUTE);
    }
  }, [query.isError, router]);

  return query;
}
