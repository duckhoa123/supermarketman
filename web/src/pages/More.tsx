import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { signOut } from "../lib/auth";

/** Phone-only page with the links that don't fit in the bottom tabs. */
export default function More({ links, children }: { links: { to: string; label: string }[]; children?: ReactNode }) {
  return (
    <>
      <div className="page-head">
        <h1>More</h1>
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
