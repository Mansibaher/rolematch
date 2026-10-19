"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, ApiError, type User } from "@/lib/api";
import { cx, Loading } from "./ui";

const UserCtx = createContext<{ user: User; refresh: () => Promise<void> } | null>(null);

export function useUser() {
  const ctx = useContext(UserCtx);
  if (!ctx) throw new Error("useUser outside Shell");
  return ctx;
}

const NAV = [
  { href: "/", label: "Today" },
  { href: "/jobs", label: "Jobs" },
  { href: "/applications", label: "Applications" },
  { href: "/profile", label: "Resume profile" },
  { href: "/preferences", label: "Preferences" },
  { href: "/sources", label: "Companies & sources" },
  { href: "/analytics", label: "Outcomes" },
  { href: "/settings", label: "Settings" },
];

export function Shell({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [open, setOpen] = useState(false);
  const router = useRouter();
  const path = usePathname();

  const refresh = useCallback(async () => {
    try {
      setUser(await api<User>("/api/auth/me"));
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) router.replace(`/login?next=${encodeURIComponent(path)}`);
    }
  }, [router, path]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => setOpen(false), [path]);

  if (!user) return <div className="mx-auto max-w-md px-6"><Loading label="Checking your session" /></div>;

  const active = (href: string) => (href === "/" ? path === "/" : path.startsWith(href));

  async function signOut() {
    await api("/api/auth/logout", { method: "POST" }).catch(() => undefined);
    router.replace("/login");
  }

  return (
    <UserCtx.Provider value={{ user, refresh }}>
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-3 focus:top-3 focus:z-50 focus:bg-panel focus:px-3 focus:py-2">
        Skip to content
      </a>
      <div className="min-h-screen lg:grid lg:grid-cols-[232px_1fr]">
        <aside className="border-b border-rule bg-panel lg:sticky lg:top-0 lg:h-screen lg:border-b-0 lg:border-r">
          <div className="flex items-center justify-between px-5 py-4 lg:block">
            <Link href="/" className="block">
              <span className="text-lg font-semibold tracking-[-0.01em]">RoleMatch</span>
              <span className="block text-xs text-muted">Evidence-based job search</span>
            </Link>
            <button type="button" className="rounded-md border border-rule-strong px-3 py-1.5 text-sm lg:hidden" aria-expanded={open} aria-controls="main-nav" onClick={() => setOpen((v) => !v)}>
              Menu
            </button>
          </div>
          <nav id="main-nav" aria-label="Main" className={cx("px-3 pb-4 lg:block", open ? "block" : "hidden")}>
            <ul className="space-y-0.5">
              {NAV.map((n) => (
                <li key={n.href}>
                  <Link
                    href={n.href}
                    aria-current={active(n.href) ? "page" : undefined}
                    className={cx(
                      "block rounded-md px-3 py-2 text-sm",
                      active(n.href) ? "bg-pine-wash font-semibold text-pine-deep" : "text-ink hover:bg-paper",
                    )}
                  >
                    {n.label}
                  </Link>
                </li>
              ))}
            </ul>
            <div className="mt-6 border-t border-rule px-3 pt-4 text-xs text-muted">
              {user.is_demo && <p className="mb-2 rounded bg-partial-wash px-2 py-1 text-ink">Demo account with synthetic data</p>}
              <p className="truncate" title={user.email}>{user.email}</p>
              <button type="button" onClick={signOut} className="mt-1 text-pine hover:underline">
                Sign out
              </button>
            </div>
          </nav>
        </aside>
        <main id="main" className="min-w-0 px-5 py-7 sm:px-8 lg:px-10">
          <div className="mx-auto max-w-6xl">{children}</div>
        </main>
      </div>
    </UserCtx.Provider>
  );
}
