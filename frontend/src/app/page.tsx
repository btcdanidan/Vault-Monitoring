"use client";

import { useRouter } from "next/navigation";
import { useSupabase } from "@/providers/supabase-provider";

export default function Home() {
  const router = useRouter();
  const supabase = useSupabase();

  async function handleSignOut() {
    await supabase.auth.signOut();
    router.push("/login");
    router.refresh();
  }

  return (
    <main className="min-h-screen p-8">
      <div className="flex justify-between items-start">
        <div>
          <h1 className="text-2xl font-bold">DeFi Vault Intelligence Platform</h1>
          <p className="mt-2 text-gray-600">Dashboard and vault analytics.</p>
        </div>
        <button
          type="button"
          onClick={handleSignOut}
          className="rounded-md border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50"
        >
          Sign out
        </button>
      </div>
    </main>
  );
}
