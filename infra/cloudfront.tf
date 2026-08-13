resource "aws_cloudfront_origin_access_control" "frontend" {
  name                              = "diginyaya-frontend-${var.environment}"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_distribution" "frontend" {
  enabled             = true
  default_root_object = "index.html"
  comment             = "DigiNyaya frontend (${var.environment})"

  origin {
    domain_name              = aws_s3_bucket.frontend.bucket_regional_domain_name
    origin_id                = "diginyaya-frontend-s3"
    origin_access_control_id = aws_cloudfront_origin_access_control.frontend.id
  }

  default_cache_behavior {
    allowed_methods        = ["GET", "HEAD"]
    cached_methods          = ["GET", "HEAD"]
    target_origin_id        = "diginyaya-frontend-s3"
    viewer_protocol_policy  = "redirect-to-https"
    compress                = true

    forwarded_values {
      query_string = false
      cookies {
        forward = "none"
      }
    }
  }

  # SPA client-side routing: a deep link like /reviewer/DN-123 has no
  # matching S3 object, so S3 returns 403 (private bucket) -- rewrite both
  # to index.html with a 200 so React Router can take over client-side.
  custom_error_response {
    error_code         = 403
    response_code      = 200
    response_page_path = "/index.html"
  }

  custom_error_response {
    error_code         = 404
    response_code      = 200
    response_page_path = "/index.html"
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    # Default *.cloudfront.net cert. A custom domain needs an ACM
    # certificate issued in us-east-1 specifically (CloudFront requirement,
    # regardless of this distribution's own region) -- deferred until
    # diginyaya.in ownership is confirmed, see the migration plan.
    cloudfront_default_certificate = true
  }
}
