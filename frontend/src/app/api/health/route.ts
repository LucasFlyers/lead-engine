import { NextResponse } from "next/server";

export async function GET() {
  const backendUrl = process.env.BACKEND_URL || process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1";
  const base = backendUrl.replace(/\/api\/v1\/?$/, "").replace(/\/$/, "");

  try {
    const res = await fetch(`${base}/health`, {
      headers: { "X-API-Key": process.env.API_SECRET_KEY || "" },
      cache: "no-store",
    });
    const data = await res.json();
    return NextResponse.json(data);
  } catch {
    return NextResponse.json({ status: "error", database: "unreachable" }, { status: 503 });
  }
}
