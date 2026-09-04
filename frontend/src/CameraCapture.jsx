import React, { useEffect, useRef, useState } from "react";

// Live camera capture (getUserMedia). Prefers the rear camera on phones.
// Grabs a still frame to a canvas -> Blob -> hands it up as a File.
export default function CameraCapture({ onCapture }) {
  const videoRef = useRef(null);
  const streamRef = useRef(null);
  const [on, setOn] = useState(false);
  const [err, setErr] = useState("");

  // Attach the stream AFTER the <video> element is mounted (on === true).
  // Setting srcObject inside start() fails because the element isn't rendered
  // yet, which is what produced the black screen.
  useEffect(() => {
    const v = videoRef.current;
    if (on && v && streamRef.current) {
      v.srcObject = streamRef.current;
      v.play().catch(() => {});
    }
  }, [on]);

  async function start() {
    setErr("");
    try {
      streamRef.current = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: "environment" }, width: { ideal: 1920 } },
        audio: false,
      });
      setOn(true); // mounts <video>; the effect above attaches the stream
    } catch (e) {
      setErr(
        "Camera unavailable - " +
          (e && e.name === "NotAllowedError"
            ? "permission denied. Allow camera access, or use file upload."
            : e && e.name === "NotFoundError"
            ? "no camera found on this device."
            : String((e && e.message) || e)) +
          (location.protocol !== "https:" && location.hostname !== "localhost"
            ? " (camera needs HTTPS or localhost.)"
            : "")
      );
    }
  }

  function stop() {
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
    if (videoRef.current) videoRef.current.srcObject = null;
    setOn(false);
  }

  function capture() {
    const v = videoRef.current;
    if (!v || !v.videoWidth) return;
    const canvas = document.createElement("canvas");
    canvas.width = v.videoWidth;
    canvas.height = v.videoHeight;
    canvas.getContext("2d").drawImage(v, 0, 0);
    canvas.toBlob(
      (blob) => {
        if (!blob) return;
        onCapture(new File([blob], `capture-${Date.now()}.jpg`, { type: "image/jpeg" }));
        stop();
      },
      "image/jpeg",
      0.92
    );
  }

  useEffect(() => stop, []); // stop the stream on unmount

  return (
    <div className="camera">
      {err && <p className="err">{err}</p>}
      {on ? (
        <>
          <div className="viewfinder">
            <video ref={videoRef} autoPlay playsInline muted />
            <div className="guide">Frame the product + the ArUco card, both flat</div>
          </div>
          <div className="camrow">
            <button type="button" onClick={capture}>Capture</button>
            <button type="button" className="ghost" onClick={stop}>Cancel</button>
          </div>
        </>
      ) : (
        <button type="button" onClick={start}>Open camera</button>
      )}
    </div>
  );
}
