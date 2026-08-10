import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  images: {
    // Photographs come from Wikimedia Commons under Creative Commons licences,
    // sourced offline by `tripplan fetch-photos`. Scoped to the exact upload host
    // rather than a wildcard so a bad URL in the data cannot turn the image
    // optimiser into an open proxy.
    remotePatterns: [
      {
        protocol: "https",
        hostname: "upload.wikimedia.org",
        pathname: "/wikipedia/commons/**",
      },
    ],
  },
};

export default nextConfig;
