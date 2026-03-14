import { NextResponse } from "next/server";

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const backendUrl = process.env.BACKEND_URL || "https://backend-api-production-a8fb.up.railway.app";
  const apiKey = process.env.API_SECRET_KEY || process.env.NEXT_PUBLIC_API_KEY || "";
  const params = searchParams.toString();

  try {
    const res = await fetch(`${backendUrl}/api/v1/pain-signals${params ? "?" + params : ""}`, {
      headers: { "X-API-Key": apiKey },
      cache: "no-store",
    });
    const data = await res.json();
    return NextResponse.json(data);
  } catch {
    return NextResponse.json({ signals: [], total: 0 }, { status: 503 });
  }
}
