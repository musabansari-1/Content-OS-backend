import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.youtube_transcripts import (
    _extract_audio_chunks_from_video,
    _extract_audio_from_video,
    _merge_chunked_transcription_bundles,
    transcribe_uploaded_video_with_artifacts,
)


class ExtractAudioFromVideoTests(unittest.TestCase):
    def test_extract_audio_uses_ffmpeg_pcm_wav_for_transcription(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            video_path = Path(temp_dir) / "input.mp4"
            video_path.write_bytes(b"video")

            with patch("app.youtube_transcripts.subprocess.run") as mock_run:
                output_path = _extract_audio_from_video(video_path, Path(temp_dir))

        command = mock_run.call_args.args[0]
        self.assertEqual(command[0], "ffmpeg")
        self.assertIn("-vn", command)
        self.assertIn("pcm_s16le", command)
        self.assertIn("16000", command)
        self.assertIn("1", command)
        self.assertEqual(output_path.suffix, ".wav")

    def test_extract_audio_surfaces_ffmpeg_stderr_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            video_path = Path(temp_dir) / "input.mp4"
            video_path.write_bytes(b"video")

            with patch(
                "app.youtube_transcripts.subprocess.run",
                side_effect=subprocess.CalledProcessError(
                    1,
                    ["ffmpeg"],
                    stderr="decoder failure",
                ),
            ):
                with self.assertRaises(RuntimeError) as context:
                    _extract_audio_from_video(video_path, Path(temp_dir))

        self.assertIn("decoder failure", str(context.exception))

    def test_extract_audio_chunks_uses_ffmpeg_segmented_wav_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            video_path = Path(temp_dir) / "input.mp4"
            video_path.write_bytes(b"video")

            def create_chunk(*_args, **_kwargs):  # noqa: ANN002, ANN003, ANN202
                chunk_dirs = sorted(Path(temp_dir).glob("transcription-audio-*"))
                self.assertTrue(chunk_dirs)
                (chunk_dirs[0] / "chunk-00000.wav").write_bytes(b"audio")

            with patch("app.youtube_transcripts.subprocess.run", side_effect=create_chunk) as mock_run:
                output_paths = _extract_audio_chunks_from_video(video_path, Path(temp_dir), 480)

        command = mock_run.call_args.args[0]
        self.assertIn("-f", command)
        self.assertIn("segment", command)
        self.assertIn("-segment_time", command)
        self.assertIn("480", command)
        self.assertEqual(len(output_paths), 1)
        self.assertEqual(output_paths[0].suffix, ".wav")


class TranscribeUploadedVideoTests(unittest.TestCase):
    def test_uploaded_video_prefers_audio_first_transcription(self) -> None:
        fake_upload = SimpleNamespace(
            filename="clip.mp4",
            content_type="video/mp4",
            file=SimpleNamespace(),
        )
        stored_path = Path("stored.mp4")
        audio_path = Path("audio.wav")
        saved_path = Path("bundle.json")
        bundle = {"text": "hello world", "segments": []}

        with patch("app.youtube_transcripts._store_uploaded_video", return_value=stored_path), patch(
            "app.youtube_transcripts._transcription_chunk_seconds", return_value=480
        ), patch(
            "app.youtube_transcripts._extract_audio_chunks_from_video", return_value=[audio_path]
        ) as mock_extract, patch(
            "app.youtube_transcripts._transcribe_audio_chunks_with_groq_bundle", return_value=dict(bundle)
        ) as mock_transcribe_audio, patch(
            "app.youtube_transcripts._transcribe_media_with_groq_bundle"
        ) as mock_transcribe_video, patch(
            "app.youtube_transcripts._save_groq_transcription_bundle", return_value=saved_path
        ):
            text, transcription_path, result_bundle, returned_stored_path = transcribe_uploaded_video_with_artifacts(
                fake_upload
            )

        mock_extract.assert_called_once_with(stored_path, stored_path.parent, 480)
        mock_transcribe_audio.assert_called_once_with([audio_path], 480)
        mock_transcribe_video.assert_not_called()
        self.assertEqual(text, "hello world")
        self.assertEqual(transcription_path, saved_path)
        self.assertEqual(returned_stored_path, stored_path)
        self.assertEqual(result_bundle["source"], "audio_first")

    def test_uploaded_video_falls_back_to_direct_video_if_audio_first_fails(self) -> None:
        fake_upload = SimpleNamespace(
            filename="clip.mp4",
            content_type="video/mp4",
            file=SimpleNamespace(),
        )
        stored_path = Path("stored.mp4")
        saved_path = Path("bundle.json")

        with patch("app.youtube_transcripts._store_uploaded_video", return_value=stored_path), patch(
            "app.youtube_transcripts._transcription_chunk_seconds", return_value=480
        ), patch(
            "app.youtube_transcripts._extract_audio_chunks_from_video",
            side_effect=RuntimeError("ffmpeg failed"),
        ), patch(
            "app.youtube_transcripts._transcribe_media_with_groq_bundle",
            return_value={"text": "fallback transcript", "segments": []},
        ) as mock_transcribe_video, patch(
            "app.youtube_transcripts._save_groq_transcription_bundle", return_value=saved_path
        ):
            text, transcription_path, result_bundle, returned_stored_path = transcribe_uploaded_video_with_artifacts(
                fake_upload
            )

        mock_transcribe_video.assert_called_once_with(stored_path)
        self.assertEqual(text, "fallback transcript")
        self.assertEqual(transcription_path, saved_path)
        self.assertEqual(returned_stored_path, stored_path)
        self.assertEqual(result_bundle["source"], "video_fallback")

    def test_uploaded_video_retries_smaller_chunks_before_video_fallback(self) -> None:
        fake_upload = SimpleNamespace(
            filename="clip.mp4",
            content_type="video/mp4",
            file=SimpleNamespace(),
        )
        stored_path = Path("stored.mp4")
        saved_path = Path("bundle.json")
        first_chunk = Path("chunk-1.wav")
        second_chunk = Path("chunk-2.wav")

        with patch("app.youtube_transcripts._store_uploaded_video", return_value=stored_path), patch(
            "app.youtube_transcripts._transcription_chunk_seconds", return_value=480
        ), patch(
            "app.youtube_transcripts._extract_audio_chunks_from_video",
            side_effect=[[first_chunk], [second_chunk]],
        ) as mock_extract, patch(
            "app.youtube_transcripts._transcribe_audio_chunks_with_groq_bundle",
            side_effect=[
                RuntimeError("413 request_too_large"),
                {"text": "smaller chunk worked", "segments": []},
            ],
        ), patch(
            "app.youtube_transcripts._transcribe_media_with_groq_bundle"
        ) as mock_transcribe_video, patch(
            "app.youtube_transcripts._save_groq_transcription_bundle", return_value=saved_path
        ):
            text, _transcription_path, result_bundle, _stored_path = transcribe_uploaded_video_with_artifacts(
                fake_upload
            )

        self.assertEqual(mock_extract.call_args_list[0].args, (stored_path, stored_path.parent, 480))
        self.assertEqual(mock_extract.call_args_list[1].args, (stored_path, stored_path.parent, 240.0))
        mock_transcribe_video.assert_not_called()
        self.assertEqual(text, "smaller chunk worked")
        self.assertEqual(result_bundle["source"], "audio_first")

    def test_chunked_audio_merge_offsets_timestamps_to_original_timeline(self) -> None:
        merged = _merge_chunked_transcription_bundles(
            [
                {
                    "text": "first chunk",
                    "segments": [{"start": 1.0, "end": 2.0, "text": "first chunk"}],
                    "words": [{"word": "first", "start": 1.0, "end": 1.4}],
                },
                {
                    "text": "second chunk",
                    "segments": [{"start": 0.5, "end": 1.5, "text": "second chunk"}],
                    "words": [{"word": "second", "start": 0.5, "end": 0.9}],
                },
            ],
            chunk_seconds=480,
        )

        self.assertEqual(merged["text"], "first chunk second chunk")
        self.assertEqual(merged["segments"][0]["start"], 1.0)
        self.assertEqual(merged["segments"][1]["start"], 480.5)
        self.assertEqual(merged["words"][1]["start"], 480.5)
        self.assertEqual(merged["source"], "chunked_audio")
        self.assertEqual(merged["chunk_count"], 2)
