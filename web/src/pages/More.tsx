import { Link } from "react-router-dom";
import type { Location } from "../lib/api";
import { signOut } from "../lib/auth";

export default function More({
  locations,
  current,
  onSwitch,
}: {
  locations: Location[];
  current: string;
  onSwitch: (id: string) => void;
}) {
  return (
    <>
      <div className="page-head">
        <h1>More</h1>
      </div>
      <section className="panel">
        <ul className="list">
          <li>
            <Link to="/orders">Orders</Link>
          </li>
          <li>
            <Link to="/tables">Tables</Link>
          </li>
          <li>
            <Link to="/settings">Settings and connections</Link>
          </li>
          {locations.length > 1 && (
            <li className="stack" style={{ gap: 6 }}>
              <span className="small muted">Restaurant</span>
              <select className="select" value={current} onChange={(e) => onSwitch(e.target.value)}>
                {locations.map((l) => (
                  <option key={l.id} value={l.id}>
                    {l.name}
                  </option>
                ))}
              </select>
            </li>
          )}
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
