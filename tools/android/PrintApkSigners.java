import com.android.apksig.ApkVerifier;

import java.io.File;
import java.security.MessageDigest;
import java.security.cert.X509Certificate;

/** Minimal CLI around Google's apksig library for archive provenance checks. */
public final class PrintApkSigners {
    private static String hex(byte[] bytes) {
        StringBuilder result = new StringBuilder(bytes.length * 2);
        for (byte value : bytes) {
            result.append(String.format("%02x", value));
        }
        return result.toString();
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 1) {
            System.err.println("usage: PrintApkSigners APK");
            System.exit(2);
        }
        ApkVerifier.Result result = new ApkVerifier.Builder(new File(args[0])).build().verify();
        System.out.printf("verified=%s%n", result.isVerified());
        System.out.printf("v1=%s%n", result.isVerifiedUsingV1Scheme());
        System.out.printf("v2=%s%n", result.isVerifiedUsingV2Scheme());
        System.out.printf("v3=%s%n", result.isVerifiedUsingV3Scheme());
        System.out.printf("v4=%s%n", result.isVerifiedUsingV4Scheme());
        int index = 0;
        for (X509Certificate certificate : result.getSignerCertificates()) {
            index++;
            System.out.printf("signer.%d.sha256=%s%n", index,
                    hex(MessageDigest.getInstance("SHA-256").digest(certificate.getEncoded())));
            System.out.printf("signer.%d.subject=%s%n", index,
                    certificate.getSubjectX500Principal().getName());
        }
        for (ApkVerifier.IssueWithParams warning : result.getWarnings()) {
            System.out.printf("warning=%s%n", warning);
        }
        for (ApkVerifier.IssueWithParams error : result.getErrors()) {
            System.out.printf("error=%s%n", error);
        }
        if (!result.isVerified()) {
            System.exit(1);
        }
    }
}
