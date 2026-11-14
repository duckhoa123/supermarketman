import { useEffect, useRef, useState } from "react";
import { Dialog } from "./PosDialogs";

type Detector = { detect: (source: HTMLVideoElement) => Promise<{ rawValue: string }[]> };
type DetectorClass = new (opts?: { formats?: string[] }) => Detector;

export const canScan = () => typeof window !== "undefined" && "BarcodeDetector" in window && !!navigator.mediaDevices?.getUserMedia;

/** Scan a barcode with the phone's camera (where the browser supports it). A handheld scanner also works: it
 * types the code into the search box. */
export default function BarcodeScan({ onCode, onClose }: { onCode: (code: string) => void; onClose: () => void }) {
  const video = useRef<HTMLVideoElement>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let stream: MediaStream | null = null;
    let live = true;
    let timer = 0;
    (async () => {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
        if (!live || !video.current) return;
        video.current.srcObject = stream;
        await video.current.play();
        const Cls = (window as unknown as { BarcodeDetector: DetectorClass }).BarcodeDetector;
        const detector = new Cls({ formats: ["ean_13", "ean_8", "upc_a", "upc_e", "code_128", "code_39", "qr_code"] });
        const tick = async () => {
          if (!live || !video.current) return;
          try {
            const found = await detector.detect(video.current);
            if (found[0]?.rawValue) {
              onCode(found[0].rawValue);
              return;
            }
          } catch { /* a frame that couldn't be read */ }
          timer = window.setTimeout(tick, 250);
        };
        void tick();
      } catch {
        setError("Couldn't open the camera. Allow camera access, or type the code in the search box.");
      }
    })();
    return () => {
      live = false;
      window.clearTimeout(timer);
      stream?.getTracks().forEach((t) => t.stop());
    };
  }, [onCode]);

  return (
    <Dialog title="Scan a barcode" onClose={onClose} narrow>
      <video ref={video} className="scan-video" muted playsInline />
      {error ? <p className="small error">{error}</p> : <p className="small muted">Point the camera at the barcode.</p>}
    </Dialog>
  );
}
