/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  // Placeholders for static generation when env is unset (e.g. CI). Runtime uses .env.
  env: {
    NEXT_PUBLIC_SUPABASE_URL:
      process.env.NEXT_PUBLIC_SUPABASE_URL || "https://placeholder.supabase.co",
    NEXT_PUBLIC_SUPABASE_ANON_KEY:
      process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || "placeholder-anon-key",
  },
};

module.exports = nextConfig;
