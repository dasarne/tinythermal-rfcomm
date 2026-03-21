import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;

import SevenZip.Compression.LZMA.Encoder;

public class ApkLzmaEncode {
    public static void main(String[] args) throws Exception {
        ByteArrayOutputStream inputBuffer = new ByteArrayOutputStream();
        byte[] tmp = new byte[8192];
        int n;
        while ((n = System.in.read(tmp)) != -1) {
            inputBuffer.write(tmp, 0, n);
        }

        byte[] input = inputBuffer.toByteArray();
        Encoder encoder = new Encoder();
        if (!encoder.SetAlgorithm(2)) {
            throw new IllegalStateException("SetAlgorithm failed");
        }
        if (!encoder.SetDictionarySize(8192)) {
            throw new IllegalStateException("SetDictionarySize failed");
        }
        if (!encoder.SetNumFastBytes(128)) {
            throw new IllegalStateException("SetNumFastBytes failed");
        }
        if (!encoder.SetMatchFinder(1)) {
            throw new IllegalStateException("SetMatchFinder failed");
        }
        if (!encoder.SetLcLpPb(3, 0, 2)) {
            throw new IllegalStateException("SetLcLpPb failed");
        }
        encoder.SetEndMarkerMode(false);

        ByteArrayOutputStream out = new ByteArrayOutputStream();
        encoder.WriteCoderProperties(out);
        long fileSize = input.length;
        for (int i = 0; i < 8; i++) {
            out.write((int) (fileSize >>> (8 * i)) & 0xFF);
        }
        encoder.Code(new ByteArrayInputStream(input), out, -1, -1, null);
        System.out.write(out.toByteArray());
    }
}
