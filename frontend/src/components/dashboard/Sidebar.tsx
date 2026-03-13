"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import {
  LayoutDashboard, Users, Zap, Mail, BarChart3,
  Activity, Inbox, Settings, ChevronRight, Menu, X
} from "lucide-react";
import { ThemeToggle } from "@/components/ui/ThemeToggle";

const nav = [
  { href: "/",             label: "Overview",     icon: LayoutDashboard },
  { href: "/leads",        label: "Leads",        icon: Users },
  { href: "/pain-signals", label: "Pain Signals", icon: Zap },
  { href: "/campaigns",    label: "Campaigns",    icon: BarChart3 },
  { href: "/inbox",        label: "Deliverability", icon: Inbox },
  { href: "/activity",     label: "Activity",     icon: Activity },
];

function NavLink({ href, label, icon: Icon, active }: { href: string; label: string; icon: React.ElementType; active: boolean }) {
  return (
    <Link
      href={href}
      className={`nav-item ${active ? "active" : ""}`}
    >
      <Icon size={15} strokeWidth={active ? 2.2 : 1.8} />
      <span style={{ flex: 1 }}>{label}</span>
      {active && <ChevronRight size={12} style={{ opacity: 0.5 }} />}
    </Link>
  );
}

export function Sidebar() {
  const pathname = usePathname();
  const [mobileOpen, setMobileOpen] = useState(false);

  const sidebar = (
    <aside style={{
      width: 220, height: "100vh", position: "sticky", top: 0,
      display: "flex", flexDirection: "column",
      background: "var(--surface)", borderRight: "1px solid var(--border)",
      flexShrink: 0,
    }}>
      {/* Logo */}
      <div style={{ padding: "18px 16px 14px", borderBottom: "1px solid var(--border)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div style={{
            width: 30, height: 30, borderRadius: 8,
            background: "var(--accent-muted)", border: "1px solid var(--accent-ring)",
            display: "flex", alignItems: "center", justifyContent: "center",
          }}>
            <Zap size={14} style={{ color: "var(--accent)" }} />
          </div>
          <div>
            <p className="font-display" style={{ fontSize: 14, fontWeight: 700, color: "var(--text-1)", lineHeight: 1.2 }}>
              LeadEngine
            </p>
            <p style={{ fontSize: 11, color: "var(--text-4)", marginTop: 1 }}>Autonomous Outreach</p>
          </div>
        </div>
      </div>

      {/* Live status pill */}
      <div style={{ padding: "10px 12px 0" }}>
        <div style={{
          display: "flex", alignItems: "center", gap: 7,
          background: "var(--green-bg)", border: "1px solid var(--green-ring)",
          borderRadius: 8, padding: "7px 10px",
        }}>
          <span className="status-dot green" />
          <span style={{ fontSize: 12, fontWeight: 500, color: "var(--green)" }}>Pipeline Active</span>
        </div>
      </div>

      {/* Navigation */}
      <nav style={{ flex: 1, padding: "10px 10px 0", display: "flex", flexDirection: "column", gap: 2 }}>
        <p style={{ fontSize: 10.5, fontWeight: 600, letterSpacing: "0.07em", textTransform: "uppercase", color: "var(--text-4)", padding: "6px 10px 4px" }}>
          Workspace
        </p>
        {nav.map(item => (
          <NavLink key={item.href} {...item} active={pathname === item.href} />
        ))}
      </nav>

      {/* Bottom bar */}
      <div style={{ padding: "12px 10px", borderTop: "1px solid var(--border)", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <Link href="/settings" className="nav-item" style={{ flex: 1, marginRight: 8, padding: "6px 8px" }}>
          <Settings size={14} />
          <span>Settings</span>
        </Link>
        <ThemeToggle />
      </div>
    </aside>
  );

  return (
    <>
      {/* Desktop */}
      <div className="hidden-mobile">{sidebar}</div>

      {/* Mobile toggle */}
      <button
        className="mobile-menu-btn"
        onClick={() => setMobileOpen(true)}
        style={{
          display: "none", position: "fixed", top: 14, left: 14, zIndex: 60,
          background: "var(--surface)", border: "1px solid var(--border)",
          borderRadius: 8, padding: 7, cursor: "pointer",
          color: "var(--text-1)",
        }}
      >
        <Menu size={18} />
      </button>

      {/* Mobile overlay */}
      {mobileOpen && (
        <div style={{ position: "fixed", inset: 0, zIndex: 50, display: "flex" }}>
          <div
            onClick={() => setMobileOpen(false)}
            style={{ position: "absolute", inset: 0, background: "rgba(0,0,0,0.5)" }}
          />
          <div style={{ position: "relative", zIndex: 51 }}>
            {sidebar}
          </div>
          <button onClick={() => setMobileOpen(false)} style={{
            position: "absolute", top: 14, right: 14,
            background: "var(--surface-3)", border: "1px solid var(--border)",
            borderRadius: 8, padding: 6, cursor: "pointer", color: "var(--text-1)",
          }}>
            <X size={16} />
          </button>
        </div>
      )}

      <style>{`
        @media (max-width: 768px) {
          .hidden-mobile { display: none !important; }
          .mobile-menu-btn { display: flex !important; }
        }
      `}</style>
    </>
  );
}
