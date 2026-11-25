import { Navigate } from "react-router-dom";
import { useApp } from "../context";
import MenuItems from "./inventory/MenuItems";

/** The dishes and drinks you sell: their recipes, plate costs and food cost %. Managers and owners only. */
export default function MenuItemsPage() {
  const { location } = useApp();
  if (location.role === "staff") return <Navigate replace to="/inventory" />;
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Menu</h1>
          <p>What you sell, with each dish's recipe, plate cost and food cost. Import your menu to set them up.</p>
        </div>
      </div>
      <MenuItems />
    </>
  );
}
