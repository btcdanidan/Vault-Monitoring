"use client";

import { useCallback, useState } from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { getSessionToken, apiGet, apiPost } from "@/lib/api";

type StatusFilter = "all" | "pending" | "approved" | "rejected";

interface ProfileListItem {
  id: string;
  email: string;
  display_name: string | null;
  approved: boolean;
  rejected: boolean;
  is_admin: boolean;
  created_at: string;
  approved_at: string | null;
}

interface AccountListResponse {
  accounts: ProfileListItem[];
  total: number;
}

function statusBadge(account: ProfileListItem) {
  if (account.rejected) {
    return (
      <span className="inline-flex rounded-full bg-red-100 px-2 py-0.5 text-xs font-medium text-red-800">
        Rejected
      </span>
    );
  }
  if (account.approved) {
    return (
      <span className="inline-flex rounded-full bg-green-100 px-2 py-0.5 text-xs font-medium text-green-800">
        Approved
      </span>
    );
  }
  return (
    <span className="inline-flex rounded-full bg-yellow-100 px-2 py-0.5 text-xs font-medium text-yellow-800">
      Pending
    </span>
  );
}

function formatDate(iso: string) {
  try {
    return new Date(iso).toLocaleString(undefined, {
      dateStyle: "short",
      timeStyle: "short",
    });
  } catch {
    return iso;
  }
}

export default function AdminAccountsPage() {
  const queryClient = useQueryClient();
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [actionError, setActionError] = useState<string | null>(null);
  const [confirmReject, setConfirmReject] = useState<string | null>(null);

  const fetchAccounts = useCallback(async (): Promise<AccountListResponse> => {
    const token = await getSessionToken();
    if (!token) throw new Error("Not authenticated");
    const path =
      statusFilter === "all"
        ? "/admin/accounts"
        : `/admin/accounts?status=${statusFilter}`;
    return apiGet<AccountListResponse>(path, { accessToken: token });
  }, [statusFilter]);

  const { data, isLoading, error } = useQuery({
    queryKey: ["admin", "accounts", statusFilter],
    queryFn: fetchAccounts,
  });

  const approve = useCallback(
    async (userId: string) => {
      setActionError(null);
      const token = await getSessionToken();
      if (!token) return;
      try {
        await apiPost<unknown>(
          `/admin/accounts/${userId}/approve`,
          {},
          { accessToken: token }
        );
        await queryClient.invalidateQueries({ queryKey: ["admin", "accounts"] });
      } catch (e) {
        setActionError(e instanceof Error ? e.message : "Failed to approve");
      }
    },
    [queryClient]
  );

  const reject = useCallback(
    async (userId: string) => {
      setActionError(null);
      setConfirmReject(null);
      const token = await getSessionToken();
      if (!token) return;
      try {
        await apiPost<unknown>(
          `/admin/accounts/${userId}/reject`,
          {},
          { accessToken: token }
        );
        await queryClient.invalidateQueries({ queryKey: ["admin", "accounts"] });
      } catch (e) {
        setActionError(e instanceof Error ? e.message : "Failed to reject");
      }
    },
    [queryClient]
  );

  return (
    <main className="min-h-screen p-8">
      <div className="mx-auto max-w-4xl">
        <div className="mb-6 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold">Account management</h1>
            <p className="mt-1 text-sm text-gray-600">
              Approve or reject signup requests. Only admins can access this
              page.
            </p>
          </div>
          <Link
            href="/"
            className="text-sm font-medium text-gray-700 underline hover:text-gray-900"
          >
            Back to dashboard
          </Link>
        </div>

        <div className="mb-4 flex gap-2">
          {(["all", "pending", "approved", "rejected"] as const).map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => setStatusFilter(s)}
              className={`rounded-md px-3 py-1.5 text-sm font-medium ${
                statusFilter === s
                  ? "bg-gray-900 text-white"
                  : "bg-gray-100 text-gray-700 hover:bg-gray-200"
              }`}
            >
              {s.charAt(0).toUpperCase() + s.slice(1)}
            </button>
          ))}
        </div>

        {actionError && (
          <p className="mb-4 text-sm text-red-600" role="alert">
            {actionError}
          </p>
        )}

        {isLoading && (
          <p className="text-gray-500">Loading accounts…</p>
        )}
        {error && (
          <p className="text-red-600">
            {error instanceof Error ? error.message : "Failed to load accounts"}
          </p>
        )}
        {data && !isLoading && (
          <div className="overflow-hidden rounded-lg border border-gray-200">
            <table className="min-w-full divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-4 py-2 text-left text-xs font-medium uppercase text-gray-500">
                    Email
                  </th>
                  <th className="px-4 py-2 text-left text-xs font-medium uppercase text-gray-500">
                    Signed up
                  </th>
                  <th className="px-4 py-2 text-left text-xs font-medium uppercase text-gray-500">
                    Status
                  </th>
                  <th className="px-4 py-2 text-right text-xs font-medium uppercase text-gray-500">
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200 bg-white">
                {data.accounts.length === 0 ? (
                  <tr>
                    <td
                      colSpan={4}
                      className="px-4 py-6 text-center text-sm text-gray-500"
                    >
                      No accounts match the filter.
                    </td>
                  </tr>
                ) : (
                  data.accounts.map((account) => (
                    <tr key={account.id}>
                      <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-900">
                        {account.email}
                        {account.is_admin && (
                          <span className="ml-1 text-gray-500">(admin)</span>
                        )}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-600">
                        {formatDate(account.created_at)}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-sm">
                        {statusBadge(account)}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-right text-sm">
                        {confirmReject === account.id ? (
                          <span className="flex justify-end gap-2">
                            <button
                              type="button"
                              onClick={() => reject(account.id)}
                              className="font-medium text-red-600 hover:text-red-700"
                            >
                              Confirm reject
                            </button>
                            <button
                              type="button"
                              onClick={() => setConfirmReject(null)}
                              className="text-gray-600 hover:text-gray-800"
                            >
                              Cancel
                            </button>
                          </span>
                        ) : (
                          <span className="flex justify-end gap-2">
                            {!account.approved && !account.rejected && (
                              <>
                                <button
                                  type="button"
                                  onClick={() => approve(account.id)}
                                  className="font-medium text-green-600 hover:text-green-700 disabled:opacity-50"
                                  disabled={account.is_admin}
                                >
                                  Approve
                                </button>
                                {!account.is_admin && (
                                  <button
                                    type="button"
                                    onClick={() =>
                                      setConfirmReject(account.id)
                                    }
                                    className="font-medium text-red-600 hover:text-red-700"
                                  >
                                    Reject
                                  </button>
                                )}
                              </>
                            )}
                          </span>
                        )}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        )}
        {data && (
          <p className="mt-2 text-sm text-gray-500">
            Total: {data.total} account{data.total !== 1 ? "s" : ""}
          </p>
        )}
      </div>
    </main>
  );
}
