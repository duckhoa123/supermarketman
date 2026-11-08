import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { signOut } from "../lib/auth";

/** Phone-only page with the classic screens: everything the agent reads from, to look at yourself. */
export default function More({ links, children }: { links: { to: string; label: string }[]; children?: ReactNode }) {
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Screens</h1>
          <p>Everything your agent reads from, to look at or change yourself.</p>
        </div>
      </div>
      <section className="panel">
        <ul className="list">
          {links.map((l) => (
            <li key={l.to}>
              <Link to={l.to}>{l.label}</Link>
            </li>
          ))}
          {children}
          <li>
            <button className="btn" onClick={() => void signOut().then(() => window.location.assign("/"))}>
              Sign out
            </button>
          </li>
        </ul>
      </section>
    </>
  );
}
