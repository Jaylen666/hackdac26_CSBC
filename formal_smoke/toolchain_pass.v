module toolchain_pass(input clk, input a);
  always @(posedge clk) begin
    assert(a == a);
  end
endmodule
