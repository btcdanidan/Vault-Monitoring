"use client";

import Link from "next/link";
import { createClient } from "@/lib/supabase/client";
import { useRouter } from "next/navigation";

export default function PendingApprovalPage() {
  const router = useRouter();

  async function handleSignOut() {
    const supabase = createClient();
    await supabase.auth.signOut();
    router.push("/login");
    router.refresh();
  }

  return (
    <main className="min-h-screen flex flex-col items-center justify-center p-8">
      <div className="w-full max-w-sm space-y-6 text-center">
        <h1 className="text-2xl font-bold">Pending approval</h1>
        <p className="text-gray-600 text-sm">
          Your account is pending admin approval. You will be able to access the app once an administrator approves your account.
        </p>
        <button
          type="button"
          onClick={handleSignOut}
          className="rounded-md bg-gray-900 px-4 py-2 text-sm font-medium text-white"
        >
          Sign out
        </button>
        <p className="text-sm text-gray-500">
          <Link href="/login" className="underline">
            Back to sign in
          </Link>
        </p>
      </div>
    </main>
  );
}
