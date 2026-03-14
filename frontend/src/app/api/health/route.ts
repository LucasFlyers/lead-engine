import { NextResponse } from "next/server";
const BACKEND = process.env.BACKEND_URL || "http://localhost:8000";
const API_KEY = process.env.API_SECRET_KEY || "";
export async function GET() {
  try {
    const res = await fetch(`${BACKEND}/health`, {
      headers: { "X-API-Key": API_KEY },
      cache: "no-store",
    });
    const data = await res.json();
    return NextResponse.json(data);
  } catch {
    return NextResponse.json({ status: "error", database: "unreachable" }, { status: 503 });
  }
}
